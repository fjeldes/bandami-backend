import logging
import sys
from app.core.config import get_settings

settings = get_settings()

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s rid=%(request_id)s — %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        from app.core.context import get_request_id
        record.request_id = get_request_id() or "-"
        return True


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    handler.addFilter(RequestIDFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    for noisy in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore",
                   "openai", "google.auth", "stripe", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
