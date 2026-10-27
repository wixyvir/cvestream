"""Structured logging (structlog) with token redaction.

Events are named `<area>.<past-participle>`. Nothing is logged here yet; later
stories add events. Never log a feed token or a full feed path: the redaction
processor is the safety net, not a licence.
"""

import logging
import re
from typing import Any, Literal

import structlog
from structlog.typing import EventDict, WrappedLogger

REDACTED = "[redacted]"
# The segment after /feeds/ is always a token (feed tokens never contain a
# dot), whatever the extension: `/feeds/<token>`, `.xml`, a mistyped `.rss`.
_FEED_PATH = re.compile(r"/feeds/[^/\s\"'?#.]+")
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def _is_token_key(key: str) -> bool:
    lowered = key.lower()
    return lowered == "token" or lowered.endswith("_token")


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _FEED_PATH.sub(f"/feeds/{REDACTED}", value)
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _is_token_key(k) else _redact_value(v)
            for k, v in value.items()
        }
    # Rebuild plain containers: subclasses such as named tuples or struct
    # sequences cannot be built from an iterable.
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(v) for v in value)
    if isinstance(value, set | frozenset):
        return {_redact_value(v) for v in value}
    return value


def redact_tokens(
    logger: WrappedLogger | None, method_name: str, event_dict: EventDict
) -> EventDict:
    """Mask `token` / `*_token` values (any case) and `/feeds/<token>` paths."""
    return _redact_value(event_dict)


def configure_logging(
    level: Literal["debug", "info", "warning", "error"],
    fmt: Literal["json", "console"],
) -> None:
    """Configure structlog to write to stdout in JSON or console format."""
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Format exceptions before redacting so tracebacks are masked too.
            structlog.processors.format_exc_info,
            redact_tokens,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_LEVELS[level]),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
