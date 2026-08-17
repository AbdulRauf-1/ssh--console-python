"""SSH Console's HTTP layer: a small server-rendered UI (Jinja2 + htmx + xterm.js)
bound to localhost. No accounts and no login — it is a single-user local tool — so
the handlers are thin wrappers over the store and the SSH engine.
"""

from .app import create_app

__all__ = ["create_app"]
