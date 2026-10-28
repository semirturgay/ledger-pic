import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.body_limit import BodySizeLimit
from app.api.errors import register_exception_handlers
from app.api.routes import auth, health, receipts
from app.core.config import settings
from app.core.logging import configure_logging, request_id_var

configure_logging(settings.log_level)
_LOCAL_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?$")
if not settings.turnstile_secret_key and any(not _LOCAL_ORIGIN.match(o) for o in settings.cors_origin_list):
    # Public frontend, no human check: every anonymous sign-in costs a 64 MiB password hash.
    logging.getLogger(__name__).warning("TURNSTILE_SECRET_KEY is empty but CORS_ORIGINS lists a public site: set up Turnstile")

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


# The API never calls the vision model: uploads are queued and app/workers/ processes them.
# /docs and /openapi.json list the public routes only; turn them off in production with API_DOCS=false.
docs = settings.api_docs
app = FastAPI(
    title="LedgerPic API",
    docs_url="/docs" if docs else None,
    redoc_url="/redoc" if docs else None,
    openapi_url="/openapi.json" if docs else None,
)
register_exception_handlers(app)
# Innermost, so FastAPI reads the body straight through it (the http middlewares below wrap
# receive in a task group, which would turn its 413 into a generic error).
app.add_middleware(BodySizeLimit, upload_limit=settings.max_upload_bytes)


@app.middleware("http")
async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    # Stored images are served with their sniffed type; never let a browser second-guess it.
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# Added last so it wraps everything above, including the 413 response.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "ngrok-skip-browser-warning", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(receipts.router)
