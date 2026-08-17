"""The SSH engine proper: the one dial-and-pin path (``Manager.connect``) that the
terminal, the file manager and the command runner all go through, so host-key
pinning cannot be bypassed by adding a feature.

Built on AsyncSSH. Host keys are verified in ``validate_host_public_key`` DURING the
handshake — before any credential is sent — reproducing the Go original's
``HostKeyCallback`` and its trust-on-first-use pinning. There is no equivalent of
``InsecureIgnoreHostKey`` anywhere in this file, by design.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import stat as statmod
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

import asyncssh

from . import dialguard

# Dial + output caps. The remote side is not trusted to be well-behaved.
_DIAL_TIMEOUT = 12                 # seconds
_MAX_PROBE_OUTPUT = 1 << 20        # 1 MiB — the identity probe
_MAX_COMMAND_OUTPUT = 4 << 20      # 4 MiB — ad-hoc run
_DEFAULT_EXEC_TIMEOUT = 300        # seconds, backstop for deadline-less callers
_MAX_EDIT_BYTES = 1 << 20          # 1 MiB — in-browser text edit ceiling


class ManagerError(Exception):
    """A connection or SSH-engine error surfaced to the caller."""


class HostKeyMismatch(ManagerError):
    """The server presented a different host key than the pinned one — a possible
    MITM, so the connection is refused."""

    def __init__(self):
        super().__init__("host key mismatch (possible MITM) — connection refused")


@dataclass
class FileEntry:
    """One directory entry in the remote file browser."""
    name: str
    size: int
    mode: str
    is_dir: bool
    is_link: bool
    mod_time: datetime | None


def _normalize_key(key) -> str:
    """A host key as a stable authorized_keys-style ``"type base64"`` value, dropping
    any trailing comment, so the pin compares cleanly."""
    presented = key.export_public_key().decode("utf-8", "replace").strip()
    parts = presented.split()
    return " ".join(parts[:2]) if len(parts) >= 2 else presented


def _is_hostkey_error(e: Exception) -> bool:
    """Whether an AsyncSSH connect error is a host-key-not-trusted failure — i.e. the
    presented key did not match the pinned one. AsyncSSH raises this during the
    handshake, before authentication."""
    cls = getattr(asyncssh, "HostKeyNotVerifiable", ())
    return isinstance(e, cls) or type(e).__name__ == "HostKeyNotVerifiable"


class Manager:
    """Owns the one dial-and-pin path."""

    def __init__(self, store, log: logging.Logger | None = None):
        self._store = store
        self._log = log or logging.getLogger("ssh_console.remote")

    # --- the one dial-and-pin path -----------------------------------------

    def _auth_kwargs(self, conn) -> dict:
        """Build the AsyncSSH auth options from the stored credential. ``client_keys``
        is set explicitly (never left to default to ~/.ssh or an agent), so only the
        stored credential is ever offered."""
        if conn.auth_type == "password":
            return {"password": conn.secret.decode("utf-8", "replace"), "client_keys": []}
        if conn.auth_type == "key":
            passphrase = conn.passphrase.decode("utf-8") if conn.passphrase else None
            try:
                key = asyncssh.import_private_key(conn.secret, passphrase)
            except Exception as e:  # noqa: BLE001 — surface any key-parse failure
                raise ManagerError(f"parse private key: {e}") from e
            return {"client_keys": [key]}
        raise ManagerError(f"unknown auth type {conn.auth_type!r}")

    @staticmethod
    def _classify(e: Exception) -> str:
        if isinstance(e, asyncssh.PermissionDenied):
            return "auth_failed"
        if "auth" in str(e).lower():
            return "auth_failed"
        return "offline"

    async def connect(self, id: str):
        """Resolve the credential, dial the server, verify/pin the host key, and
        return ``(SSHClientConnection, SSHConn)``. Updates the server's status as a
        side effect. The caller owns closing the returned connection."""
        conn_meta = self._store.get_ssh_conn(id)
        if conn_meta is None:
            raise ManagerError("ssh server not found")

        try:
            auth = self._auth_kwargs(conn_meta)
        except ManagerError as e:
            self._store.update_ssh_server_status(id, "auth_failed", str(e))
            raise

        # Abuse guard: refuse link-local / cloud-metadata targets before connecting;
        # resolve once and connect to the vetted address (closes DNS rebinding).
        try:
            vetted_ip = await asyncio.to_thread(
                dialguard.resolve_and_vet, conn_meta.host, conn_meta.port
            )
        except dialguard.BlockedTarget as e:
            self._store.update_ssh_server_status(id, "offline", str(e))
            raise ManagerError(str(e)) from e
        except socket.gaierror as e:
            self._store.update_ssh_server_status(id, "offline", str(e))
            raise ManagerError(f"dial {conn_meta.host}:{conn_meta.port}: {e}") from e

        # Host-key verification:
        #  - pinned already → hand the key to AsyncSSH as known_hosts, so it rejects a
        #    mismatch DURING the handshake (before any credential is sent);
        #  - no pin yet → connect with checking off, then read and pin the key (TOFU).
        pinned = (conn_meta.host_key or "").strip()
        known_hosts = ("* " + pinned + "\n").encode() if pinned else None

        try:
            conn = await asyncssh.connect(
                host=vetted_ip,
                port=conn_meta.port,
                username=conn_meta.username,
                known_hosts=known_hosts,
                agent_path=None,           # never fall back to an ssh-agent
                connect_timeout=_DIAL_TIMEOUT,
                **auth,
            )
        except Exception as e:  # noqa: BLE001
            if pinned and _is_hostkey_error(e):
                self._store.update_ssh_server_status(id, "hostkey_mismatch", str(HostKeyMismatch()))
                raise HostKeyMismatch() from e
            status = self._classify(e)
            self._store.update_ssh_server_status(id, status, str(e))
            raise ManagerError(str(e)) from e

        # Trust-on-first-use: persist the host key the first time we see it.
        if not pinned:
            try:
                learned = _normalize_key(conn.get_server_host_key())
                if learned:
                    self._store.pin_ssh_host_key(id, learned)
                    self._log.info("pinned ssh host key (TOFU) server=%s", id)
            except Exception as e:  # noqa: BLE001
                self._log.warning("pin host key server=%s err=%s", id, e)

        self._store.update_ssh_server_status(id, "online", "")
        return conn, conn_meta

    # --- bounded exec ------------------------------------------------------

    async def _run_on(self, conn, cmd: str, max_output: int, *, input_bytes: bytes | None = None,
                      timeout: int = _DEFAULT_EXEC_TIMEOUT) -> tuple[str, str | None]:
        """Run a command over SSH, bounded in both time and output size. Returns
        ``(output, error_message_or_None)`` — command-level failures come back as a
        value, not an exception, mirroring the Go original."""
        buf = bytearray()
        truncated = False

        async with conn.create_process(cmd, stderr=asyncssh.STDOUT, encoding=None) as proc:
            if input_bytes is not None:
                proc.stdin.write(input_bytes)
                proc.stdin.write_eof()

            async def pump():
                nonlocal truncated
                while True:
                    chunk = await proc.stdout.read(8192)
                    if not chunk:
                        break
                    room = max_output - len(buf)
                    if room > 0:
                        buf.extend(chunk[:room])
                        if len(chunk) > room:
                            truncated = True
                    else:
                        truncated = True

            try:
                await asyncio.wait_for(pump(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.terminate()
                return self._finish_output(buf, truncated), f"command timed out after {timeout}s"

            await proc.wait_closed()
            err = None
            if proc.exit_status not in (0, None):
                err = f"process exited with status {proc.exit_status}"
        return self._finish_output(buf, truncated), err

    @staticmethod
    def _finish_output(buf: bytearray, truncated: bool) -> str:
        s = buf.decode("utf-8", "replace")
        if truncated:
            s += "\n[output truncated by SSH Console]"
        return s

    async def test_connection(self, id: str) -> str:
        """Connect and run a harmless identity command. Returns a short banner."""
        conn, _ = await self.connect(id)
        try:
            out, err = await self._run_on(conn, "id; uname -a 2>/dev/null || true", _MAX_PROBE_OUTPUT)
        finally:
            conn.close()
        if err:
            self._store.update_ssh_server_status(id, "offline", err)
            raise ManagerError(err)
        return out.strip()

    async def run_command(self, id: str, cmd: str, timeout: int = 60) -> tuple[str, str | None]:
        """Execute a non-interactive command over SSH and return ``(output, err)``."""
        conn, _ = await self.connect(id)
        try:
            return await self._run_on(conn, cmd, _MAX_COMMAND_OUTPUT, timeout=timeout)
        finally:
            conn.close()

    # --- SFTP --------------------------------------------------------------

    @asynccontextmanager
    async def _sftp(self, id: str):
        conn, _ = await self.connect(id)
        sftp = await conn.start_sftp_client()
        try:
            yield sftp
        finally:
            sftp.exit()
            conn.close()

    @staticmethod
    def _as_str(name) -> str:
        return name.decode("utf-8", "replace") if isinstance(name, (bytes, bytearray)) else name

    async def list_dir(self, id: str, dir: str) -> tuple[str, list[FileEntry]]:
        """List a remote directory. An empty path resolves to the login home. Returns
        the resolved absolute path and its entries (dirs first, then by name)."""
        async with self._sftp(id) as sftp:
            if not dir:
                dir = "."
            try:
                resolved = self._as_str(await sftp.realpath(dir))
                if resolved:
                    dir = resolved
            except asyncssh.SFTPError:
                pass
            names = await sftp.readdir(dir)
            entries: list[FileEntry] = []
            for n in names:
                fname = self._as_str(n.filename)
                if fname in (".", ".."):
                    continue
                attrs = n.attrs
                perm = attrs.permissions or 0
                entries.append(FileEntry(
                    name=fname,
                    size=attrs.size or 0,
                    mode=statmod.filemode(perm),
                    is_dir=statmod.S_ISDIR(perm),
                    is_link=statmod.S_ISLNK(perm),
                    mod_time=datetime.fromtimestamp(attrs.mtime) if attrs.mtime else None,
                ))
            entries.sort(key=lambda e: (not e.is_dir, e.name))
            return dir, entries

    async def download(self, id: str, remote_path: str):
        """Open a remote file for streaming. Returns ``(size, async_iterator)``; the
        iterator owns the SSH+SFTP session and closes it when exhausted."""
        conn, _ = await self.connect(id)
        sftp = await conn.start_sftp_client()
        try:
            st = await sftp.stat(remote_path)
            if statmod.S_ISDIR(st.permissions or 0):
                raise ManagerError("that path is a directory")
            size = st.size or 0
            f = await sftp.open(remote_path, "rb")
        except Exception:
            sftp.exit()
            conn.close()
            raise

        async def gen():
            try:
                while True:
                    chunk = await f.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                await f.close()
                sftp.exit()
                conn.close()

        return size, gen()

    async def upload(self, id: str, remote_path: str, data: bytes) -> int:
        """Write data to remote_path on the server (creating/truncating it)."""
        async with self._sftp(id) as sftp:
            async with sftp.open(remote_path, "wb") as f:
                await f.write(data)
            return len(data)

    async def mkdir(self, id: str, dir: str) -> None:
        """Create a directory (and any missing parents) on the server."""
        async with self._sftp(id) as sftp:
            await sftp.makedirs(dir, exist_ok=True)

    async def create_file(self, id: str, remote_path: str) -> None:
        """Create a new empty file, refusing to touch a path that already exists, so
        it can never truncate an existing file or directory."""
        async with self._sftp(id) as sftp:
            exists = True
            try:
                await sftp.lstat(remote_path)
            except asyncssh.SFTPError:
                exists = False
            if exists:
                raise ManagerError(f'"{posixpath.basename(remote_path)}" already exists')
            f = await sftp.open(remote_path, "xb")  # exclusive create
            await f.close()

    async def rename(self, id: str, old_path: str, new_path: str) -> None:
        """Move/rename a remote path."""
        async with self._sftp(id) as sftp:
            await sftp.rename(old_path, new_path)

    async def remove(self, id: str, target: str) -> None:
        """Delete a file, or a directory (recursively). The caller must apply the
        delete guard (clean + root/denylist) before calling this."""
        async with self._sftp(id) as sftp:
            st = await sftp.lstat(target)
            if statmod.S_ISDIR(st.permissions or 0):
                await sftp.rmtree(target)
            else:
                await sftp.remove(target)

    async def read_text(self, id: str, remote_path: str, max_bytes: int = _MAX_EDIT_BYTES) -> bytes:
        """Read a remote file for in-browser editing, refusing files larger than
        max_bytes, directories, and binary files."""
        async with self._sftp(id) as sftp:
            try:
                st = await sftp.stat(remote_path)
                if statmod.S_ISDIR(st.permissions or 0):
                    raise ManagerError("that path is a directory")
                if (st.size or 0) > max_bytes:
                    raise ManagerError(
                        "file is too large to edit in the browser — download it instead")
            except asyncssh.SFTPError:
                pass
            async with sftp.open(remote_path, "rb") as f:
                body = await f.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ManagerError(
                    "file is too large to edit in the browser — download it instead")
            if _is_binary(body):
                raise ManagerError(
                    "this looks like a binary file — download it instead of editing")
            return body


def _is_binary(data: bytes) -> bool:
    """Whether data looks non-textual: a NUL byte in the first 8 KiB is the standard
    heuristic (git uses the same)."""
    return b"\x00" in data[:8192]


def parent(p: str) -> str:
    """Parent directory of a remote path (POSIX semantics)."""
    if not p or p == "/":
        return "/"
    return posixpath.dirname(p)


def join(dir: str, name: str) -> str:
    """Join a remote directory and name with POSIX separators."""
    return posixpath.join(dir, name)
