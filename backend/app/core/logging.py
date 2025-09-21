import logging
import os
from typing import Optional

_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _make_console_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _make_json_formatter() -> logging.Formatter:
    # Lightweight JSON-ish formatter without extra deps
    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            # keep it simple and robust
            msg = super().format(record)
            return (
                '{"ts":"%s","level":"%s","logger":"%s","msg":%s}'
                % (
                    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                    record.levelname,
                    record.name,
                    # ensure quoted JSON string; escape quotes
                    '"' + msg.replace('"', '\\"') + '"',
                )
            )

    return JsonFormatter("%(message)s")


def setup_logging(
    level: Optional[str] = None,
    fmt: Optional[str] = None,
) -> None:
    """
    Initialize root logging once.
    Env overrides:
      LOG_LEVEL = INFO|DEBUG|...
      LOG_FORMAT = text|json
    """
    level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    fmt = (fmt or os.getenv("LOG_FORMAT") or "text").lower()

    log_level = _LEVELS.get(level, logging.INFO)
    formatter = _make_json_formatter() if fmt == "json" else _make_console_formatter()

    root = logging.getLogger()
    # clear existing handlers (uvicorn adds its own — we align them)
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(log_level)

    # Align uvicorn loggers too
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(log_level)