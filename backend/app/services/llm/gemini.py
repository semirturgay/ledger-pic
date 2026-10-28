"""Google Gemini through the google-genai SDK."""

import logging
from typing import Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import Settings
from app.services.llm.base import MAX_OUTPUT_TOKENS, ExtractionFailed, Unconfigured, VisionModel

log = logging.getLogger(__name__)


class GeminiVision:
    def __init__(self, client: genai.Client, model: str) -> None:
        self._client, self.name = client, model

    @classmethod
    def from_settings(cls, settings: Settings) -> VisionModel:
        if not settings.gemini_api_key:
            return Unconfigured(settings.gemini_model, "The vision model is not configured on the server (GEMINI_API_KEY).")
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=settings.ai_timeout_seconds * 1000,  # milliseconds
                retry_options=types.HttpRetryOptions(
                    # One quick retry for blips. Not for 429: the queue waits as long as Gemini asks.
                    attempts=2, initial_delay=1.0, max_delay=4.0, http_status_codes=[500, 502, 503, 504]
                ),
            ),
        )
        return cls(client, settings.gemini_model)

    async def read(self, image: bytes, mime_type: str, prompt: str, schema: dict[str, Any]) -> str:
        parts = [types.Part.from_bytes(data=image, mime_type=mime_type), types.Part.from_text(text=prompt)]
        try:
            response = await self._client.aio.models.generate_content(
                model=self.name,
                contents=types.Content(role="user", parts=parts),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    temperature=0,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except genai_errors.APIError as exc:
            log.warning("Gemini returned an error: code=%s message=%s", exc.code, exc.message)
            if exc.code == 429:
                wait = retry_delay_seconds(exc.details)
                raise ExtractionFailed.rate_limited(wait) from exc
            if exc.code and exc.code >= 500:
                raise ExtractionFailed.busy() from exc
            raise ExtractionFailed.rejected(exc.code, "Gemini") from exc
        except httpx.TimeoutException as exc:
            log.warning("Gemini request timed out: %r", exc)
            raise ExtractionFailed.took_too_long() from exc
        except httpx.HTTPError as exc:
            log.warning("Gemini request failed: %r", exc)
            raise ExtractionFailed.unreachable() from exc
        # Text is None when the response was blocked or empty; validation treats that as malformed.
        return response.text or ""


def retry_delay_seconds(details: Any) -> float | None:
    """Gemini's suggested wait from a google.rpc.RetryInfo detail, e.g. {"retryDelay": "37s"}."""
    for detail in (details or {}).get("error", {}).get("details", []):
        if str(detail.get("retryDelay", "")).endswith("s"):
            return float(detail["retryDelay"][:-1])
    return None
