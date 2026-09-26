"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .api import admin, auth, cases, health, reviews, slides, stats, users
from .core.config import get_settings
from .core.logging import request_id_var, setup_logging
from .core.metrics import API_REGISTRY, HTTP_ERRORS, HTTP_LATENCY
from .core.security import CSRF_COOKIE, CSRF_HEADER, csrf_matches
from .schemas import DISCLAIMER

log = logging.getLogger("api")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_EXEMPT = {"/auth/login"}  # no session yet; login CSRF is blocked by SameSite=strict + CORS

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'"
)


def create_app() -> FastAPI:
    s = get_settings()
    s.check_production()
    setup_logging(s.LOG_LEVEL)
    if s.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(dsn=s.SENTRY_DSN, send_default_pii=False, traces_sample_rate=0.0, before_send=_scrub_event)

    app = FastAPI(
        title="GleasonAI API",
        version="1.0.0",
        description=f"Prostate biopsy AI review service (thesis FedAvg ensemble). **{DISCLAIMER}**",
        openapi_url=f"{s.API_PREFIX}/openapi.json" if s.docs_enabled else None,
        docs_url=f"{s.API_PREFIX}/docs" if s.docs_enabled else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER, "Content-Range", "X-Request-ID"],
        expose_headers=["Content-Disposition", "X-Request-ID", "Retry-After"],
    )

    @app.middleware("http")
    async def middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        rid = rid[:64]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        path = request.url.path
        try:
            if (
                request.method not in SAFE_METHODS
                and path.startswith(s.API_PREFIX)
                and path[len(s.API_PREFIX) :] not in CSRF_EXEMPT
                and not csrf_matches(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER))
            ):
                response: Response = JSONResponse({"detail": "Missing or invalid CSRF token."}, status_code=403)
            else:
                response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"path": path})
            response = JSONResponse({"detail": "Internal server error.", "request_id": rid}, status_code=500)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - start
        HTTP_LATENCY.labels(request.method, route_path, str(response.status_code)).observe(elapsed)
        if response.status_code >= 500:
            HTTP_ERRORS.labels(route_path).inc()
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if route_path.endswith("/docs") or path.endswith("/openapi.json"):
            response.headers["Content-Security-Policy"] = DOCS_CSP
        if s.COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        if path.startswith(s.API_PREFIX) and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "method": request.method,
                "route": route_path,
                "status": response.status_code,
                "ms": round(elapsed * 1000, 1),
                "user_id": getattr(request.state, "user_id", None),
            },
        )
        request_id_var.reset(token)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": "Invalid input.", "errors": errors}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    for r in (
        auth.router,
        users.router,
        cases.router,
        cases.uploads_router,
        reviews.router,
        slides.router,
        stats.router,
        admin.router,
        health.router,
    ):
        app.include_router(r, prefix=s.API_PREFIX)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(API_REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _scrub_event(event: Any, _hint: dict[str, Any]) -> Any:
    """Sentry: drop request bodies, cookies and query strings (may contain patient codes)."""
    req = event.get("request") or {}
    for k in ("data", "cookies", "query_string", "headers"):
        req.pop(k, None)
    event.pop("user", None)
    return event


app = create_app()
