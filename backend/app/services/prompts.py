import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import CATEGORIES, Prompt, PromptType
from app.services.llm import ExtractionFailed


@dataclass(frozen=True)
class ActivePrompt:
    version: int
    text: str


class PromptStore:
    """Active prompt per type from the `prompts` table, cached per process for `ttl_seconds`."""

    def __init__(self, session_factory: Callable[[], AsyncSession] = SessionLocal, ttl_seconds: float = 60) -> None:
        self._session_factory = session_factory
        self._ttl = ttl_seconds
        self._cache: dict[PromptType, tuple[float, ActivePrompt]] = {}

    async def active(self, prompt_type: PromptType) -> ActivePrompt:
        cached = self._cache.get(prompt_type)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        async with self._session_factory() as db:
            row = await db.scalar(select(Prompt).where(Prompt.type == prompt_type, Prompt.is_active))
        if row is None:
            raise ExtractionFailed(f"No active '{prompt_type}' prompt is configured.", retryable=False)
        # replace(), not format(): templates may contain JSON examples with literal braces.
        prompt = ActivePrompt(row.version, row.template.replace("{categories}", ", ".join(CATEGORIES)))
        self._cache[prompt_type] = (time.monotonic() + self._ttl, prompt)
        return prompt

    def clear(self) -> None:
        self._cache.clear()


prompt_store = PromptStore(ttl_seconds=settings.prompt_cache_seconds)
