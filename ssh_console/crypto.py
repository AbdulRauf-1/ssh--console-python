"""Envelope encryption for the secrets SSH Console stores at rest: SSH credentials
(passwords, private keys, passphrases) and terminal recordings. Everything is
sealed with AES-256-GCM under one master key.

The master key is generated on first run and kept in a local key file next to the
database (see config). It never leaves the machine — SSH Console runs on your own
laptop, so the disk is yours — but the credentials it protects can contain real
secrets, so they are never written in plaintext.

The on-disk blob layout is deliberately identical to the Go original:

    version(1) || nonce(12) || AES-256-GCM(ciphertext || tag)

with the purpose and owning scope mixed into the AEAD's additional authenticated
data, so a blob written for one column/row cannot be opened in another.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Purpose names what a ciphertext is, so a blob written for one column cannot be
# pasted into another and opened. The purpose and the owning scope (the server id,
# or SCOPE_GLOBAL) go into the AEAD's additional authenticated data.
PURPOSE_SSH_SECRET = "ssh_secret"      # password or private key PEM
PURPOSE_SSH_PASSPHRASE = "ssh_passphrase"  # private-key passphrase
PURPOSE_RECORDING = "recording"        # a terminal session transcript

# SCOPE_GLOBAL is the scope for secrets not tied to a single server.
SCOPE_GLOBAL = "global"

_ENVELOPE_V1 = 0x01
_NONCE_SIZE = 12  # AES-GCM standard nonce, matches Go's gcm.NonceSize()


class CryptoError(Exception):
    """Raised on malformed ciphertext or a wrong-length key."""


def new_master_key() -> str:
    """Return a fresh 32-byte master key, base64-encoded. Seeds the key file."""
    return base64.standard_b64encode(os.urandom(32)).decode("ascii")


def parse_master_key(b64: str) -> bytes:
    """Decode a base64 32-byte master key. Surrounding whitespace is ignored, so a
    trailing newline in the key file is not a reason to refuse to start."""
    b64 = (b64 or "").strip()
    if not b64:
        raise CryptoError("master key not set")
    try:
        key = base64.standard_b64decode(b64)
    except Exception as e:  # noqa: BLE001 — surface any decode failure uniformly
        raise CryptoError(f"decode master key: {e}") from e
    if len(key) != 32:
        raise CryptoError(f"master key must be 32 bytes, got {len(key)}")
    return key


def _aad(purpose: str, scope: str) -> bytes:
    """Additional authenticated data: version, purpose and scope, length-delimited
    so ("ssh", "abc") and ("sshabc", "") cannot collide."""
    p = purpose.encode("utf-8")
    s = scope.encode("utf-8")
    return bytes([_ENVELOPE_V1, len(p)]) + p + bytes([len(s)]) + s


def _require_key(key: bytes) -> None:
    if len(key) != 32:
        raise CryptoError(f"key must be 32 bytes for AES-256, got {len(key)}")


def encrypt_for(key: bytes, purpose: str, scope: str, plaintext: bytes) -> bytes:
    """Seal plaintext, binding it to a purpose and an owning scope (usually the
    server id). The scope must be stable and known at both encrypt and decrypt
    time."""
    _require_key(key)
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, plaintext, _aad(purpose, scope))
    return bytes([_ENVELOPE_V1]) + nonce + ct


def decrypt_for(key: bytes, purpose: str, scope: str, blob: bytes) -> bytes:
    """Reverse encrypt_for."""
    _require_key(key)
    if blob is None or len(blob) <= 1 + _NONCE_SIZE or blob[0] != _ENVELOPE_V1:
        raise CryptoError("malformed ciphertext")
    nonce = blob[1:1 + _NONCE_SIZE]
    ct = blob[1 + _NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ct, _aad(purpose, scope))
    except Exception as e:  # noqa: BLE001 — authentication failure or corruption
        raise CryptoError(f"decrypt: {e}") from e
