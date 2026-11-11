"""Structured logging: JSON lines in prod (Render captures stdout), readable text in dev."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else passed via `extra=` is emitted as a field.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class DevFormatter(logging.Formatter):
    """Human-readable, but still shows the structured `extra=` fields as key=value."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = " ".join(
            f"{k}={v}"
            for k, v in vars(record).items()
            if k not in _STANDARD_ATTRS and not k.startswith("_")
        )
        return f"{line}  {extras}" if extras else line


def configure_logging(json_logs: bool) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(DevFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # Our request middleware logs requests; uvicorn's access log would duplicate them.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("pymongo", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
