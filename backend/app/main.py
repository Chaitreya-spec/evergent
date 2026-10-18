"""FastAPI application."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger, request_id_ctx
from app.core.net import client_ip
from app.core.ratelimit import limiter
from app.services.orchestrator import events

log = get_logger(__name__)


async def _sweep_orphans() -> None:
    """Recover workflows whose engine died, now and then periodically."""
    from app.db.session import SessionFactory
    from app.services.orchestrator.recovery import recover_orphans

    while True:
        try:
            async with SessionFactory() as session:
                recovered = await recover_orphans(session)
            if recovered:
                log.warning("orphans_recovered", count=len(recovered))
        except Exception as exc:
            log.warning("orphan_sweep_failed", error=str(exc)[:200])
        await asyncio.sleep(settings.orphan_after_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info(
        "startup",
        app=settings.app_name,
        environment=settings.environment,
        api_prefix=settings.api_v1_prefix,
    )
    sweeper = None
    if settings.environment != "test":
        from app.db.session import SessionFactory
        from app.services.providers.registry import load_credentials

        try:
            async with SessionFactory() as session:
                await load_credentials(session)
        except Exception as exc:
            log.warning("provider_credentials_not_loaded", error=str(exc)[:200])
        sweeper = asyncio.create_task(_sweep_orphans())
    yield
    if sweeper is not None:
        sweeper.cancel()
    await events.close_redis()
    await limiter.close()
    log.info("shutdown")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Multi-agent AI orchestration: one goal in, understood, planned, "
        "delegated across models, validated and synthesised."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Middleware -------------------------------------------------------------

@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Assign a request id, time the request, and log the outcome."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request_id_ctx.set(request_id)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    finally:
        request_id_ctx.set(None)

    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id

    # Security headers. HSTS is omitted in development, where there is no TLS.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

    log.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
        request_id=request_id,
    )
    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path in ("/health", "/ready") or request.method == "OPTIONS":
        return await call_next(request)

    decision = await limiter.hit(client_ip(request), request.url.path)
    if not decision.allowed:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "code": "rate_limited",
                "message": "Too many requests. Slow down and try again shortly.",
                "details": {"limit_per_minute": decision.limit},
            },
            headers={"Retry-After": str(decision.retry_after)},
        )
    return await call_next(request)


# --- Exception handlers -----------------------------------------------------


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "code": "validation_error",
            "message": "The request body failed validation",
            "details": {"errors": exc.errors()},
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error=str(exc))
    # The real message is logged, never returned: an internal error must not
    # leak stack details or internal paths to a caller.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": "internal_error",
            "message": "An unexpected error occurred.",
            "details": {},
        },
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)
# Health is also mounted unprefixed, where orchestrators expect to find it.
app.include_router(
    __import__("app.api.v1.health", fromlist=["router"]).router, include_in_schema=False
)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "api": settings.api_v1_prefix,
    }
