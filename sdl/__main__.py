"""Entry point: ``python -m sdl [url]``."""

from __future__ import annotations

import logging
import os
import sys

from .config import APP_DIR
from .tui import main

LOG_FILE = APP_DIR / "sdl.log"


def _setup_logging() -> None:
    """Quiet by default; SDL_DEBUG=1 writes a full trace to sdl.log.

    Nothing is logged to the console in normal use: the interface already
    reports failures in a form the user can act on, and a traceback interleaved
    with the progress bars only makes it harder to read.
    """
    if os.getenv("SDL_DEBUG") == "1":
        logging.basicConfig(
            level=logging.DEBUG,
            filename=str(LOG_FILE),
            filemode="w",
            encoding="utf-8",
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
    else:
        logging.basicConfig(level=logging.CRITICAL)


if __name__ == "__main__":
    _setup_logging()
    url = " ".join(sys.argv[1:]).strip() or None
    try:
        sys.exit(main(url))
    except KeyboardInterrupt:
        print()
        sys.exit(130)
