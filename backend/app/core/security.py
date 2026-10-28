import datetime as dt
import hashlib
import secrets
import uuid
from typing import NamedTuple

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

# Fixed in code, not configurable: a misconfigured "none" or RS256 against an HMAC secret is impossible.
JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()  # Argon2id with the library's recommended parameters


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Verified against when the email doesn't exist, so login timing doesn't reveal registered emails.
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


def create_access_token(user_id: uuid.UUID) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + dt.timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


# Device tokens: proof that this browser signed in to the account before. They carry no access, only
# the device's own sign-in limit, so a stranger's guesses can't lock the owner out of a device they use.
# A separate key, so a device token can never pass as an access token (or the other way round).
def _device_key() -> bytes:
    return hashlib.sha256(b"ledgerpic-device-token\0" + settings.jwt_secret.encode()).digest()


class Device(NamedTuple):
    user_id: uuid.UUID
    device_id: str


def create_device_token(user_id: uuid.UUID) -> str:
    now = dt.datetime.now(dt.UTC)
    payload = {
        "sub": str(user_id),
        "jti": secrets.token_urlsafe(16),
        "iat": now,
        "exp": now + dt.timedelta(days=settings.device_token_days),
    }
    return jwt.encode(payload, _device_key(), algorithm=JWT_ALGORITHM)


def decode_device_token(token: str) -> Device | None:
    try:
        payload = jwt.decode(token, _device_key(), algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub", "jti"]})
        return Device(uuid.UUID(payload["sub"]), str(payload["jti"]))
    except (jwt.PyJWTError, ValueError, TypeError):
        return None
