import json
import logging
import sys
from app.core.config import get_settings

settings = get_settings()


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        from app.core.context import get_request_id
        record.request_id = get_request_id() or "-"
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record):
        from app.core.context import get_request_id
        return json.dumps({
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "severity": record.levelname,
            "logger": record.name,
            "request_id": get_request_id() or "-",
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }, default=str)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)

    if settings.environment == "production":
        handler.setFormatter(JSONFormatter())
    else:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s rid=%(request_id)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        handler.setFormatter(fmt)

    handler.addFilter(RequestIDFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    for noisy in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore",
                   "openai", "google.auth", "stripe", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
