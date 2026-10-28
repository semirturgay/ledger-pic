"""Caps request bodies as they stream in, declared length or not (chunked uploads have none).

Runs before FastAPI parses anything, so an oversized body is cut off before it is spooled to disk
or held in memory. Uploads must also carry a validly signed, unexpired token before any of the
body is read, so forged or missing tokens cost the server nothing.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.security import decode_access_token

# Room for multipart boundaries and headers around a full-size image.
MULTIPART_OVERHEAD = 64 * 1024
# JSON bodies (login, register, confirm with 200 line items) are far smaller than this.
JSON_LIMIT = 1024 * 1024
TOO_LARGE = "Request body is larger than the limit"


def _signed_in(authorization: bytes) -> bool:
    scheme, _, token = authorization.decode("latin-1").partition(" ")
    return scheme.lower() == "bearer" and decode_access_token(token.strip()) is not None


class BodySizeLimit:
    def __init__(self, app: ASGIApp, upload_limit: int) -> None:
        self.app = app
        self.upload_limit = upload_limit + MULTIPART_OVERHEAD

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_upload = scope["method"] == "POST" and scope["path"].rstrip("/") == "/receipts"
        limit = self.upload_limit if is_upload else JSON_LIMIT

        headers = dict(scope["headers"])
        if is_upload and not _signed_in(headers.get(b"authorization", b"")):
            # An upload without a valid token would be refused anyway: refuse it before parsing or spooling
            # up to 10 MB of it. The signature and expiry are checked here (one HMAC); whether the account
            # still exists is checked by the route as usual.
            await JSONResponse({"detail": "Not authenticated"}, status_code=401)(scope, receive, send)
            return

        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            # Fail before reading anything when the client says up front that it's too big.
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def counted_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI re-raises its own HTTPException from body parsing (other errors become a 400).
                    raise HTTPException(413, TOO_LARGE)
            return message

        await self.app(scope, counted_receive, send)
