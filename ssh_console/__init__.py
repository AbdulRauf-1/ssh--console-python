"""SSH Console — a local, single-user web console for SSH sessions, with recording.

Python port of the Go original. Same scope, same security invariants: localhost
only, no accounts, credentials and recordings encrypted at rest, host keys pinned
trust-on-first-use, one dial-and-pin path.
"""

__version__ = "0.1.0"
