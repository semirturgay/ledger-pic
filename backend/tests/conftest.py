import os
import random

from sqlalchemy.engine import make_url

# Must be set before the app is imported: settings and the engine are created at import time.
# Postgres only, like production. Each test drops every table, so refuse anything but a *_test database.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ledgerpic:ledgerpic@localhost:5432/ledgerpic_test"
)
_url = make_url(TEST_DATABASE_URL)
# No query parameters at all: libpq would take dbname/service/options from them over the path.
if not (_url.database or "").endswith("_test") or _url.query:
    raise SystemExit(f"TEST_DATABASE_URL must name a *_test database, with no query parameters (each test drops all tables): {TEST_DATABASE_URL}")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["JWT_SECRET"] = "test-secret-not-for-production-0123456789"
os.environ["GEMINI_API_KEY"] = ""
os.environ["EXTRACTION_PROVIDER"] = "gemini"  # never reach a real model server from tests, whatever .env says
# Settings also read the developer's .env: pin everything that would reach an outside service.
for _name in ("CLAUDE_API_KEY", "TURNSTILE_SECRET_KEY"):
    os.environ[_name] = ""
# Every test user is minutes old and registers from the same address: tests that check those limits
# turn them back on themselves.
os.environ["NEW_ACCOUNT_HOURS"] = "0"
os.environ["REGISTRATIONS_PER_IP_PER_DAY"] = "1000"

import datetime as dt  # noqa: E402
import importlib.util  # noqa: E402
from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: E402
from io import BytesIO  # noqa: E402
from pathlib import Path  # noqa: E402
from types import ModuleType  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.rate_limit import auth_limiter, upload_limiter  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import (
    SessionLocal,  # noqa: E402
    engine,  # noqa: E402
)
from app.main import app  # noqa: E402
from app.models import Prompt, PromptType  # noqa: E402
from app.services.extraction import ReceiptExtractor  # noqa: E402
from app.services.prompts import prompt_store  # noqa: E402
from app.workers.extraction_job import run_due_jobs  # noqa: E402
from app.workers.queue import utcnow  # noqa: E402


def _load_migration(name: str) -> ModuleType:
    """Migration files start with a digit and live beside the `alembic` package name, so load by path."""
    path = Path(__file__).parents[1] / "alembic" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_hardening = _load_migration("0008_hardening")  # the prompt immutability trigger


class FakeVision:
    """A VisionModel that answers with canned replies (or raises them), recording what it was sent."""

    name = "fake"

    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies, self.prompts, self.images = replies, [], []

    async def read(self, image: bytes, mime_type: str, prompt: str, _schema: dict) -> str:
        self.prompts.append(prompt)
        self.images.append((image, mime_type))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeExtractor(ReceiptExtractor):
    """The real extractor (validation, retry, salvage) with a FakeVision model."""

    def __init__(self, replies: list[str | Exception]) -> None:
        vision = FakeVision(replies)
        super().__init__(vision, max_concurrency=1)
        self.replies, self.prompts, self.images = vision.replies, vision.prompts, vision.images


@pytest.fixture(autouse=True)
async def fresh_state() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)  # leftovers from an interrupted run
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(_hardening.IMMUTABLE_PROMPTS_FUNCTION))
        await conn.execute(text(_hardening.IMMUTABLE_PROMPTS_TRIGGER))
    async with SessionLocal() as db:
        db.add(Prompt(type=PromptType.RECEIPT_EXTRACTION, version=1, template="Test prompt ({categories})", is_active=True))
        await db.commit()
    auth_limiter.reset()
    upload_limiter.reset()
    prompt_store.clear()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # Each test runs in its own event loop; pooled connections must not outlive it.
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture
def run_jobs() -> Callable[..., Awaitable[FakeExtractor]]:
    """Act as the worker: process every due job with a fake model, and return the fake.

    The fake answers with the given replies (str) or raises the given errors, in order.
    `later` runs the queue as if that much time had passed, for backoff and lease expiry.
    """

    async def run(*replies: str | Exception, later: dt.timedelta = dt.timedelta(0)) -> FakeExtractor:
        fake = FakeExtractor(list(replies))
        await run_due_jobs(fake, now=lambda: utcnow() + later)
        return fake

    return run


def make_image(fmt: str = "JPEG", size: tuple[int, int] = (400, 600)) -> bytes:
    buf = BytesIO()
    # A random colour per call: identical bytes would now be rejected as a duplicate upload.
    Image.new("RGB", size, tuple(random.randbytes(3))).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
async def alice(client: httpx.AsyncClient) -> dict[str, str]:
    return await auth_headers(client)


async def auth_headers(client: httpx.AsyncClient, email: str = "alice@example.com") -> dict[str, str]:
    response = await client.post("/auth/register", json={"email": email, "password": "correct-horse"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    data: bytes | None = None,
    name: str = "r.jpg",
    idempotency_key: str | None = None,
) -> httpx.Response:
    if idempotency_key:
        headers = {**headers, "Idempotency-Key": idempotency_key}
    return await client.post(
        "/receipts", headers=headers, files={"file": (name, data or make_image(), "image/jpeg")}
    )


async def new_receipt(client: httpx.AsyncClient, headers: dict[str, str], *args, **kwargs) -> str:
    response = await upload(client, headers, *args, **kwargs)
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def get(client: httpx.AsyncClient, headers: dict[str, str], receipt_id: str) -> dict:
    return (await client.get(f"/receipts/{receipt_id}", headers=headers)).json()
