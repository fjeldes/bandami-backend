from contextvars import ContextVar
from uuid import uuid4

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def set_request_id(rid: str | None = None):
    _request_id_var.set(rid or uuid4().hex[:12])


def get_request_id() -> str:
    return _request_id_var.get()
