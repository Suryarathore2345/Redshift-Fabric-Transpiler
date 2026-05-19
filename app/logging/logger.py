"""
Structured logging via structlog.

Provides:
  - get_logger()        — returns a bound structlog logger
  - configure_logging() — call once at startup
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
from structlog.types import WrappedLogger


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Configure structlog + stdlib logging."""

    log_level = getattr(logging, level.upper(), logging.INFO)

    # stdlib root logger
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if sys.stdout.isatty():
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Optional file handler
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "converter.log")
        fh.setLevel(log_level)
        fh.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(fh)


def get_logger(name: str = "converter") -> WrappedLogger:
    return structlog.get_logger(name)
