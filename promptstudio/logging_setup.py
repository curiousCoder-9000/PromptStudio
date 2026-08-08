"""Logging configuration.

``print()`` gives you nothing six hours into a rate-limited scrape: no
timestamp, no module, no level, and nothing on disk once the terminal closes.
When a following sync stops early, the question is always *which account, which
status, which backoff branch* — and that answer has to survive the session.

Handlers attach to the ``promptstudio`` logger, not the root logger, and do not
propagate. Importing this package therefore never hijacks logging for anything
that embeds it.

Configuration is lazy: the first ``get_logger()`` call installs handlers, so
importing a module has no filesystem side effect. Set ``PROMPTSTUDIO_LOG_FILE``
to an empty string to disable file logging (tests do this).
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

from promptstudio.config import (
    LOG_BACKUPS,
    LOG_CONSOLE,
    LOG_FILE,
    LOG_LEVEL,
    LOG_MAX_BYTES,
)

ROOT_NAME = "promptstudio"

_configure_lock = threading.Lock()
_configured = False

# Marks handlers this module owns, so force-reconfigure never closes someone
# else's handler.
_OWNED = "_promptstudio_owned"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(*, force: bool = False) -> logging.Logger:
    """Install handlers on the `promptstudio` logger. Idempotent."""
    global _configured

    logger = logging.getLogger(ROOT_NAME)
    with _configure_lock:
        if _configured and not force:
            return logger

        # Only tear down handlers we installed. Test harnesses and embedders
        # attach their own to this logger, and closing those would silently
        # break their capture.
        for handler in list(logger.handlers):
            if getattr(handler, _OWNED, False):
                logger.removeHandler(handler)
                handler.close()

        level = getattr(logging, str(LOG_LEVEL).upper(), logging.INFO)
        logger.setLevel(level)
        # Own the output; don't double-print through the root logger.
        logger.propagate = False

        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

        if LOG_CONSOLE:
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            setattr(console, _OWNED, True)
            logger.addHandler(console)

        if LOG_FILE:
            try:
                os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
                file_handler = RotatingFileHandler(
                    LOG_FILE,
                    maxBytes=int(LOG_MAX_BYTES),
                    backupCount=int(LOG_BACKUPS),
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                setattr(file_handler, _OWNED, True)
                logger.addHandler(file_handler)
            except OSError as e:
                # An unwritable log path must not stop the app from running.
                logger.warning("file logging disabled (%s): %s", LOG_FILE, e)

        if not any(getattr(h, _OWNED, False) for h in logger.handlers):
            null = logging.NullHandler()
            setattr(null, _OWNED, True)
            logger.addHandler(null)

        _configured = True
        return logger


def get_logger(name: str) -> logging.Logger:
    """Logger for a module, e.g. ``get_logger(__name__)``."""
    configure_logging()
    if name == ROOT_NAME or name.startswith(ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_NAME}.{name}")
