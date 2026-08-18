"""SSH Console's persistence: a single local SQLite file holding SSH connections and
terminal recordings. There is no external database — the file lives on the machine
running SSH Console (your laptop), so its disk is the only storage involved.

SSH credentials and recordings are envelope-encrypted (see crypto) with the master
key before they touch the file, so the .db on disk carries no plaintext secrets.

SQLite is a single writer, so every access goes through one connection guarded by a
lock; timestamps are stored as Unix-seconds INTEGER and surfaced as datetimes.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import crypto

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ssh_servers (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  host            TEXT NOT NULL,
  port            INTEGER NOT NULL,
  username        TEXT NOT NULL,
  auth_type       TEXT NOT NULL,              -- key | password
  secret_enc      BLOB NOT NULL,
  passphrase_enc  BLOB,
  host_key        TEXT,
  status          TEXT NOT NULL DEFAULT 'unknown',
  last_error      TEXT,
  last_checked_at INTEGER,
  created_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS terminal_sessions (
  id            TEXT PRIMARY KEY,
  server_id     TEXT NOT NULL REFERENCES ssh_servers(id) ON DELETE CASCADE,
  server_name   TEXT NOT NULL,
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER,
  bytes         INTEGER NOT NULL DEFAULT 0,
  recording_enc BLOB,
  name          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_server ON terminal_sessions(server_id);
"""


# --- types -----------------------------------------------------------------

@dataclass
class SSHServer:
    """The metadata view of a saved connection (no secret material)."""
    id: str
    name: str
    host: str
    port: int
    username: str
    auth_type: str          # key | password
    host_key: str | None
    status: str
    last_error: str | None
    last_checked_at: datetime | None
    created_at: datetime

    @property
    def host_key_set(self) -> bool:
        """Whether a host key has been pinned (TOFU done)."""
        return bool(self.host_key)


@dataclass
class SSHConn(SSHServer):
    """Metadata plus the decrypted credential — server-side use only, never
    rendered."""
    secret: bytes = b""       # private key PEM or password
    passphrase: bytes = b""   # key passphrase (may be empty)


@dataclass
class Session:
    """A recorded terminal session."""
    id: str
    server_id: str
    server_name: str
    started_at: datetime
    ended_at: datetime | None
    bytes: int = 0
    name: str = ""  # optional user-given label; empty until renamed


def _now() -> int:
    return int(time.time())


def _unix_to_dt(n) -> datetime | None:
    if n is None:
        return None
    return datetime.fromtimestamp(n)


class Store:
    """Wraps the SQLite handle and the master key used to seal/open secrets."""

    def __init__(self, path: str | Path, master_key: bytes):
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._key = master_key
        # One connection, one lock: SQLite is a single writer, and this avoids
        # "database is locked" without raising the connection count.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        # Add-column migration for databases created before the column existed.
        # SQLite has no "ADD COLUMN IF NOT EXISTS"; a duplicate is expected/ignored.
        try:
            self._db.execute("ALTER TABLE terminal_sessions ADD COLUMN name TEXT")
        except sqlite3.OperationalError:
            pass
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- seal / open -------------------------------------------------------

    def _seal(self, purpose: str, scope: str, plain: bytes) -> bytes:
        return crypto.encrypt_for(self._key, purpose, scope, plain)

    def _open(self, purpose: str, scope: str, blob: bytes) -> bytes:
        return crypto.decrypt_for(self._key, purpose, scope, blob)

    # --- server CRUD -------------------------------------------------------

    def create_ssh_server(self, name, host, port, username, auth_type,
                          secret: bytes, passphrase: bytes) -> str:
        """Store a connection, encrypting the credential + passphrase. Returns id."""
        new_id = str(uuid.uuid4())
        sec_enc = self._seal(crypto.PURPOSE_SSH_SECRET, new_id, secret)
        pass_enc = None
        if passphrase:
            pass_enc = self._seal(crypto.PURPOSE_SSH_PASSPHRASE, new_id, passphrase)
        with self._lock:
            self._db.execute(
                "INSERT INTO ssh_servers"
                "(id,name,host,port,username,auth_type,secret_enc,passphrase_enc,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,'unknown',?)",
                (new_id, name, host, port, username, auth_type, sec_enc, pass_enc, _now()),
            )
            self._db.commit()
        return new_id

    _SERVER_COLS = ("id,name,host,port,username,auth_type,host_key,status,"
                    "last_error,last_checked_at,created_at")

    @staticmethod
    def _row_to_server(r) -> SSHServer:
        return SSHServer(
            id=r[0], name=r[1], host=r[2], port=r[3], username=r[4], auth_type=r[5],
            host_key=r[6], status=r[7], last_error=r[8],
            last_checked_at=_unix_to_dt(r[9]), created_at=_unix_to_dt(r[10]),
        )

    def list_ssh_servers(self) -> list[SSHServer]:
        """Every saved connection, ordered by name."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._SERVER_COLS} FROM ssh_servers ORDER BY name"
            ).fetchall()
        return [self._row_to_server(r) for r in rows]

    def get_ssh_server(self, id: str) -> SSHServer | None:
        """One connection's metadata, or None if absent."""
        with self._lock:
            r = self._db.execute(
                f"SELECT {self._SERVER_COLS} FROM ssh_servers WHERE id=?", (id,)
            ).fetchone()
        return self._row_to_server(r) if r else None

    def get_ssh_conn(self, id: str) -> SSHConn | None:
        """Metadata plus the decrypted credential, for connecting."""
        with self._lock:
            r = self._db.execute(
                "SELECT id,name,host,port,username,auth_type,host_key,status,"
                "last_error,last_checked_at,created_at,secret_enc,passphrase_enc"
                " FROM ssh_servers WHERE id=?", (id,)
            ).fetchone()
        if not r:
            return None
        secret = self._open(crypto.PURPOSE_SSH_SECRET, id, r[11])
        passphrase = b""
        if r[12]:
            passphrase = self._open(crypto.PURPOSE_SSH_PASSPHRASE, id, r[12])
        return SSHConn(
            id=r[0], name=r[1], host=r[2], port=r[3], username=r[4], auth_type=r[5],
            host_key=r[6], status=r[7], last_error=r[8],
            last_checked_at=_unix_to_dt(r[9]), created_at=_unix_to_dt(r[10]),
            secret=secret, passphrase=passphrase,
        )

    def update_ssh_server_status(self, id: str, status: str, last_err: str) -> None:
        """Record the outcome of a connection attempt."""
        with self._lock:
            self._db.execute(
                "UPDATE ssh_servers SET status=?, last_error=?, last_checked_at=? WHERE id=?",
                (status, last_err or None, _now(), id),
            )
            self._db.commit()

    def pin_ssh_host_key(self, id: str, host_key: str) -> None:
        """Record the trusted host key learned on first connect (TOFU). Only writes
        when none is pinned yet, so a pin cannot be silently overwritten."""
        with self._lock:
            self._db.execute(
                "UPDATE ssh_servers SET host_key=? WHERE id=? AND host_key IS NULL",
                (host_key, id),
            )
            self._db.commit()

    def update_ssh_server(self, id, name, host, port, username, auth_type,
                         secret: bytes, passphrase: bytes, reset_host_key: bool) -> None:
        """Edit metadata. A non-empty secret replaces the credential; reset_host_key
        clears the pin + status so the next connect re-pins (TOFU)."""
        hk = ", host_key=NULL, status='unknown', last_error=NULL" if reset_host_key else ""
        with self._lock:
            if secret:
                sec_enc = self._seal(crypto.PURPOSE_SSH_SECRET, id, secret)
                pass_enc = None
                if passphrase:
                    pass_enc = self._seal(crypto.PURPOSE_SSH_PASSPHRASE, id, passphrase)
                self._db.execute(
                    "UPDATE ssh_servers SET name=?,host=?,port=?,username=?,auth_type=?,"
                    "secret_enc=?,passphrase_enc=?" + hk + " WHERE id=?",
                    (name, host, port, username, auth_type, sec_enc, pass_enc, id),
                )
            else:
                self._db.execute(
                    "UPDATE ssh_servers SET name=?,host=?,port=?,username=?,auth_type=?"
                    + hk + " WHERE id=?",
                    (name, host, port, username, auth_type, id),
                )
            self._db.commit()

    def rename_ssh_server(self, id: str, name: str) -> None:
        """Change only a connection's display name."""
        with self._lock:
            self._db.execute("UPDATE ssh_servers SET name=? WHERE id=?", (name, id))
            self._db.commit()

    def delete_ssh_server(self, id: str) -> None:
        """Remove a connection (and cascade its recordings)."""
        with self._lock:
            self._db.execute("DELETE FROM ssh_servers WHERE id=?", (id,))
            self._db.commit()

    # --- recordings --------------------------------------------------------

    def create_terminal_session(self, server_id: str, server_name: str) -> str:
        """Open a recording row when a terminal starts. Returns id."""
        new_id = str(uuid.uuid4())
        with self._lock:
            self._db.execute(
                "INSERT INTO terminal_sessions(id,server_id,server_name,started_at)"
                " VALUES(?,?,?,?)",
                (new_id, server_id, server_name, _now()),
            )
            self._db.commit()
        return new_id

    def finish_terminal_session(self, id: str, total_bytes: int, transcript: str) -> None:
        """Close a recording row, storing the (encrypted) captured output. An empty
        transcript keeps the row (when/how much) with no stored bytes."""
        rec_enc = None
        if transcript:
            rec_enc = self._seal(crypto.PURPOSE_RECORDING, id, transcript.encode("utf-8"))
        with self._lock:
            self._db.execute(
                "UPDATE terminal_sessions SET ended_at=?, bytes=?, recording_enc=? WHERE id=?",
                (_now(), total_bytes, rec_enc, id),
            )
            self._db.commit()

    _SESSION_COLS = "id,server_id,server_name,started_at,ended_at,bytes,name"

    @staticmethod
    def _row_to_session(r) -> Session:
        return Session(
            id=r[0], server_id=r[1], server_name=r[2],
            started_at=_unix_to_dt(r[3]), ended_at=_unix_to_dt(r[4]), bytes=r[5],
            name=r[6] or "",
        )

    def list_sessions(self, server_id: str = "") -> list[Session]:
        """Recorded sessions, newest first. server_id="" lists all."""
        q = f"SELECT {self._SESSION_COLS} FROM terminal_sessions"
        args: tuple = ()
        if server_id:
            q += " WHERE server_id=?"
            args = (server_id,)
        q += " ORDER BY started_at DESC"
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_session(self, id: str) -> tuple[Session | None, str]:
        """A session's metadata and its decrypted transcript."""
        with self._lock:
            r = self._db.execute(
                "SELECT id,server_id,server_name,started_at,ended_at,bytes,name,recording_enc"
                " FROM terminal_sessions WHERE id=?", (id,)
            ).fetchone()
        if not r:
            return None, ""
        sess = Session(
            id=r[0], server_id=r[1], server_name=r[2],
            started_at=_unix_to_dt(r[3]), ended_at=_unix_to_dt(r[4]), bytes=r[5],
            name=r[6] or "",
        )
        transcript = ""
        if r[7]:
            transcript = self._open(crypto.PURPOSE_RECORDING, id, r[7]).decode("utf-8", "replace")
        return sess, transcript

    def rename_session(self, id: str, name: str) -> None:
        """Set (or clear) a recording's user-given label."""
        with self._lock:
            self._db.execute("UPDATE terminal_sessions SET name=? WHERE id=?", (name or None, id))
            self._db.commit()

    def delete_session(self, id: str) -> None:
        """Remove a recorded session."""
        with self._lock:
            self._db.execute("DELETE FROM terminal_sessions WHERE id=?", (id,))
            self._db.commit()
