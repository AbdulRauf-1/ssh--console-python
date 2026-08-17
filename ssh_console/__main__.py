"""Command entrypoint. Run it on your own machine, open the printed URL, add a
server, and get a real terminal in the browser — every session recorded and
replayable.

    python -m ssh_console            # run the server
    python -m ssh_console version    # print the version
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from . import __version__, config
from .remote import Manager
from .store import Store
from .web import create_app


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("version", "-version", "--version"):
        print("SSH Console", __version__)
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("ssh_console")

    cfg = config.load()

    store = Store(cfg.db_path, cfg.master_key)
    manager = Manager(store, log.getChild("remote"))
    app = create_app(store, manager, cfg, log.getChild("web"))

    # Split "host:port" for uvicorn. rsplit handles IPv6-less host:port cleanly.
    host, _, port_str = cfg.listen.rpartition(":")
    if not host:
        host, port_str = cfg.listen, "8022"
    try:
        port = int(port_str)
    except ValueError:
        port = 8022

    log.info(
        "SSH Console listening version=%s url=http://%s data_dir=%s max_terminals=%s",
        __version__, cfg.listen, cfg.data_dir, cfg.max_terminals,
    )

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning", ws_max_size=1 << 20)
    finally:
        store.close()
        log.info("SSH Console stopped")


if __name__ == "__main__":
    main()
