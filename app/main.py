import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
import os
from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.context import set_request_id
from app.core.logging_config import setup_logging
from app.db.deps import get_db
from app.api.v1.routers import writing, speaking, reading, listening, users, auth, payments, admin

logger = logging.getLogger("ielts.startup")
settings = get_settings()
setup_logging()

app = FastAPI(
    title="Bandami",
    version="0.2.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
async def recover_stale_exams():
    """Reset exams stuck in 'processing' for more than 30 minutes back to 'failed'."""
    from app.db.engine import engine
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("UPDATE exams SET status = 'failed' WHERE status = 'processing' AND created_at < NOW() - INTERVAL '30 minutes'")
            )
            recovered = result.rowcount
            if recovered:
                logger.info("Recovered %d stale 'processing' exams to 'failed'", recovered)
            conn.commit()
    except Exception as e:
        logger.error("Failed to recover stale exams: %s", e)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or None
    set_request_id(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.__dict__.get("_request_id", "-")
    return response


app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret_key)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["Payments"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(writing.router, prefix="/api/v1/evaluate/writing", tags=["Writing"])
app.include_router(speaking.router, prefix="/api/v1/evaluate/speaking", tags=["Speaking"])
app.include_router(reading.router, prefix="/api/v1/evaluate/reading", tags=["Reading"])
app.include_router(listening.router, prefix="/api/v1/evaluate/listening", tags=["Listening"])


@app.get("/api/health")
async def health_check():
    from app.db.engine import engine
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": "0.2.0",
        "database": "connected" if db_ok else "unavailable",
    }


@app.get("/api/health/ready")
async def readiness_check():
    from app.db.engine import engine
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not ready", "database": "unavailable"})


# ---- Legal ----

@app.get("/legal/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms_of_service():
    path = os.path.join(os.path.dirname(__file__), "static", "terms.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


@app.get("/legal/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy():
    path = os.path.join(os.path.dirname(__file__), "static", "privacy.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


@app.get("/legal/refund", response_class=HTMLResponse, include_in_schema=False)
async def refund_policy():
    path = os.path.join(os.path.dirname(__file__), "static", "refund.html")
    with open(path, encoding="utf-8") as f:
        return f.read()
