"""Structured logging setup using Rich."""

import logging

from rich.logging import RichHandler

_LOGGER_NAME = "repricing_engine"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return the package logger with a Rich handler.

    Args:
        level: Log level name (e.g. ``"INFO"``, ``"DEBUG"``). Unknown values
            fall back to ``INFO``.

    Returns:
        The configured ``repricing_engine`` logger.
    """
    resolved = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(resolved)
    logger.propagate = False

    if not logger.handlers:
        handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(resolved)

    return logger
