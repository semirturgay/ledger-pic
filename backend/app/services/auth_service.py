import asyncio
import contextlib
import uuid
import weakref
from collections.abc import AsyncIterator, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmailAlreadyRegistered, InvalidCredentials, ServiceBusy
from app.core.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.models import User

# Argon2id uses about 64 MiB per hash: cap how many run at once in this process.
_HASHING: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _hashing() -> asyncio.Semaphore:
    return _HASHING.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(4))
HASH_WAIT_SECONDS = 10


async def _hash[T](function: Callable[..., T], *args: object) -> T:
    """Run an Argon2 call in a thread under a hashing slot. The slot is held until the thread has
    really finished, even if the request is cancelled meanwhile, so at most 4 hashes ever run."""
    async with _hash_slot():
        work = asyncio.ensure_future(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await asyncio.wait({work})
            raise


@contextlib.asynccontextmanager
async def _hash_slot() -> AsyncIterator[None]:
    """A hashing slot, or 503 after a short wait: a flood of sign-ins can't queue up without bound."""
    try:
        await asyncio.wait_for(_hashing().acquire(), HASH_WAIT_SECONDS)
    except TimeoutError:
        raise ServiceBusy("LedgerPic is busy right now. Try again in a moment.") from None
    try:
        yield
    finally:
        _hashing().release()


class AuthService:
    """Registration and login. Argon2 is CPU-heavy, so hashing runs in a thread."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register(self, email: str, password: str) -> User:
        hashed = await _hash(hash_password, password)
        user = User(email=email, password_hash=hashed)
        self.db.add(user)
        try:
            await self.db.commit()
        except IntegrityError:
            # The unique index is the source of truth; checking first would race.
            await self.db.rollback()
            raise EmailAlreadyRegistered("An account with this email already exists") from None
        return user

    async def login(self, email: str, password: str) -> User:
        user = await self.db.scalar(select(User).where(User.email == email))
        stored_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
        # Give the pooled connection back before waiting for a hashing slot: a flood of sign-ins
        # waiting here must not hold every database connection the rest of the API needs.
        await self.db.commit()
        password_ok = await _hash(verify_password, password, stored_hash)
        if user is None or not password_ok:
            raise InvalidCredentials("Invalid email or password")
        return user

    async def user_id_for(self, email: str) -> uuid.UUID | None:
        return await self.db.scalar(select(User.id).where(User.email == email))

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self.db.get(User, user_id)
