import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.core import errors
from app.core.logging import request_id_var

log = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[errors.DomainError], int] = {
    errors.NotFound: status.HTTP_404_NOT_FOUND,
    errors.InvalidStateTransition: status.HTTP_409_CONFLICT,
    errors.EmailAlreadyRegistered: status.HTTP_409_CONFLICT,
    errors.InvalidCredentials: status.HTTP_401_UNAUTHORIZED,
    errors.UnsupportedImage: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    errors.ImageTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    errors.EmptyUpload: status.HTTP_400_BAD_REQUEST,
    errors.IdempotencyKeyReused: status.HTTP_422_UNPROCESSABLE_CONTENT,
    errors.RateLimited: status.HTTP_429_TOO_MANY_REQUESTS,
    errors.DuplicateUpload: status.HTTP_409_CONFLICT,
    errors.QuotaExceeded: status.HTTP_429_TOO_MANY_REQUESTS,
    errors.CaptchaFailed: status.HTTP_400_BAD_REQUEST,
    errors.ServiceBusy: status.HTTP_503_SERVICE_UNAVAILABLE,
    errors.InvalidCursor: status.HTTP_400_BAD_REQUEST,
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(errors.DomainError)
    async def domain_error(_request: Request, exc: errors.DomainError) -> JSONResponse:
        code = STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse({"detail": exc.message, **jsonable_encoder(exc.extra)}, status_code=code)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception) -> JSONResponse:
        # Details go to the log; the client gets an id to quote, never a stack trace.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            {"detail": "Internal server error", "request_id": request_id_var.get()},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
