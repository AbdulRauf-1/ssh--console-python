"""Reads SSH Console's small configuration from the environment and resolves the
local data directory, database path and master key.

SSH Console is a single-user, run-it-locally tool: there are no accounts and it
binds to localhost by default. The only piece of real setup is the master key,
which is generated automatically on first run and stored in a 0600 file next to
the database — so "just run it" holds, while stored SSH credentials and recordings
are still encrypted at rest.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import crypto


@dataclass
class Config:
    listen: str          # host:port to bind (default 127.0.0.1:8080)
    data_dir: Path       # directory for the database + key file
    db_path: Path        # <data_dir>/ssh-console.db
    master_key: bytes    # 32 bytes, loaded or generated
    max_terminals: int   # concurrent live terminals; 0 = unlimited


def _env_or(key: str, default: str) -> str:
    v = os.environ.get(key, "").strip()
    return v if v else default


def _user_config_dir() -> Path:
    """Mirror Go's os.UserConfigDir() across the three desktop platforms."""
    if sys.platform == "win32":
        base = os.environ.get("AppData", "")
        return Path(base) if base else Path(".")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    # Linux / other Unix
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(xdg) if xdg else Path.home() / ".config"


def load() -> Config:
    """Read the environment (SSHCONSOLE_*), ensure the data directory exists, and
    load or create the master key."""
    listen = _env_or("SSHCONSOLE_LISTEN", "127.0.0.1:8022")

    data_dir_env = os.environ.get("SSHCONSOLE_DATA_DIR", "").strip()
    if data_dir_env:
        data_dir = Path(data_dir_env)
    else:
        base = _user_config_dir()
        data_dir = base / "ssh-console"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass  # best-effort on platforms without POSIX perms (Windows)

    db_path = data_dir / "ssh-console.db"

    max_terminals = 0
    v = os.environ.get("SSHCONSOLE_MAX_TERMINALS", "").strip()
    if v:
        try:
            n = int(v)
        except ValueError:
            raise SystemExit(
                f"SSHCONSOLE_MAX_TERMINALS must be a non-negative integer, got {v!r}"
            )
        if n < 0:
            raise SystemExit(
                f"SSHCONSOLE_MAX_TERMINALS must be a non-negative integer, got {v!r}"
            )
        max_terminals = n

    master_key = _load_or_create_master_key(data_dir / "master.key")

    return Config(
        listen=listen,
        data_dir=data_dir,
        db_path=db_path,
        master_key=master_key,
        max_terminals=max_terminals,
    )


def _load_or_create_master_key(path: Path) -> bytes:
    """Read the base64 master key from path, or generate one and write it (0600)
    if the file does not exist yet."""
    if path.exists():
        try:
            return crypto.parse_master_key(path.read_text(encoding="utf-8"))
        except crypto.CryptoError as e:
            raise SystemExit(f"master key file {path} is invalid: {e}")

    # First run: generate and persist.
    b64 = crypto.new_master_key()
    # Create with 0600 from the start so the key is never briefly world-readable.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, (b64 + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    return crypto.parse_master_key(b64)
