"""SSH Console's SSH engine: it dials OUT to the servers you save (holding the
credential) for a web terminal, a file manager and ad-hoc commands. Nothing is
installed on the target — if it runs sshd, it works.

Credentials are read decrypted from the store; host keys are pinned
trust-on-first-use (TOFU) and verified on every later connect, so a changed key
refuses the connection.
"""

from .manager import (
    FileEntry,
    HostKeyMismatch,
    Manager,
    parent,
    join,
)

__all__ = ["Manager", "FileEntry", "HostKeyMismatch", "parent", "join"]
