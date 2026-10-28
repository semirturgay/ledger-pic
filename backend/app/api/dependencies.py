import ipaddress
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import RateLimited
from app.core.rate_limit import auth_limiter
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User
from app.services.auth_service import AuthService
from app.services.receipt_service import ReceiptService

_bearer = HTTPBearer(auto_error=False)


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)


def get_receipt_service(db: AsyncSession = Depends(get_db)) -> ReceiptService:
    return ReceiptService(db)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    if credentials is None:
        raise _unauthorized("Not authenticated")
    user_id = decode_access_token(credentials.credentials)
    user = await auth.get_user(user_id) if user_id else None
    if user is None:
        raise _unauthorized("Invalid or expired token")
    return user


# Route parameter types, so each route says what it needs once: `user: CurrentUser, receipts: Receipts`.
CurrentUser = Annotated[User, Depends(get_current_user)]
Receipts = Annotated[ReceiptService, Depends(get_receipt_service)]
Auth = Annotated[AuthService, Depends(get_auth_service)]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit_key(request: Request) -> str:
    """The address rate limits count against. One IPv6 customer usually holds a whole /64, so IPv6
    is keyed by that prefix: otherwise every request could come from a fresh address."""
    host = client_ip(request)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return str(ipaddress.ip_network(f"{address}/64", strict=False)) if address.version == 6 else host


async def auth_rate_limit(request: Request) -> None:
    if not auth_limiter.allow(f"auth:{rate_limit_key(request)}", settings.auth_rate_limit_per_minute, 60):
        raise RateLimited("Too many attempts. Wait a minute and try again.")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})
