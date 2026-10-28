"""Anthropic Claude through the anthropic SDK, with structured outputs."""

import base64
import logging
from typing import Any, Literal, cast

import anthropic
from anthropic.types import ImageBlockParam, MessageParam, OutputConfigParam, TextBlockParam

from app.core.config import Settings
from app.services.llm.base import ExtractionFailed, Unconfigured, VisionModel

log = logging.getLogger(__name__)


class ClaudeVision:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client, self.name = client, model

    @classmethod
    def from_settings(cls, settings: Settings) -> VisionModel:
        if not settings.claude_api_key:
            return Unconfigured(settings.claude_model, "The vision model is not configured on the server (CLAUDE_API_KEY).")
        # One quick SDK retry for blips; longer waits (rate limits) go back to the queue.
        client = anthropic.AsyncAnthropic(api_key=settings.claude_api_key, timeout=settings.ai_timeout_seconds, max_retries=1)
        return cls(client, settings.claude_model)

    async def read(self, image: bytes, mime_type: str, prompt: str, schema: dict[str, Any]) -> str:
        media_type = cast(Literal["image/jpeg", "image/png"], mime_type)  # uploads are only ever JPEG or PNG
        image_block: ImageBlockParam = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(image).decode()},
        }
        prompt_block: TextBlockParam = {"type": "text", "text": prompt}
        message: MessageParam = {"role": "user", "content": [image_block, prompt_block]}
        # Extraction needs little reasoning; low effort keeps output tokens (and cost) down.
        output: OutputConfigParam = {"effort": "low", "format": {"type": "json_schema", "schema": strict_schema(schema)}}
        try:
            response = await self._client.messages.create(model=self.name, max_tokens=4096, messages=[message], output_config=output)
        except anthropic.RateLimitError as exc:
            wait = exc.response.headers.get("retry-after", "")
            retry_after = float(wait) if wait.isdigit() else None
            raise ExtractionFailed.rate_limited(retry_after) from exc
        except anthropic.APIStatusError as exc:
            log.warning("Claude returned an error: %s %s", exc.status_code, exc.message)
            if exc.status_code >= 500:  # includes 529 overloaded
                raise ExtractionFailed.busy() from exc
            raise ExtractionFailed.rejected(exc.status_code, "Claude") from exc
        except anthropic.APITimeoutError as exc:
            log.warning("Claude request timed out: %r", exc)
            raise ExtractionFailed.took_too_long() from exc
        except anthropic.APIConnectionError as exc:
            log.warning("Claude request failed: %r", exc)
            raise ExtractionFailed.unreachable() from exc
        if response.stop_reason == "refusal":
            raise ExtractionFailed("The AI declined to read this image.", retryable=False)
        # max_tokens truncation yields invalid JSON, which validation catches and retries.
        return next((block.text for block in response.content if block.type == "text"), "")


def strict_schema(node: Any) -> Any:
    """Claude's structured outputs need additionalProperties: false on objects; nullable types become anyOf."""
    if isinstance(node, list):
        return [strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: strict_schema(value) for key, value in node.items()}
    if node.get("type") == "object":
        node["additionalProperties"] = False
    if isinstance(node.get("type"), list) and "null" in node["type"]:
        rest = {key: value for key, value in node.items() if key != "type"}
        if "enum" in rest:
            rest["enum"] = [value for value in rest["enum"] if value is not None]
        base = next(t for t in node["type"] if t != "null")
        node = {"anyOf": [{"type": base, **rest}, {"type": "null"}]}
    return node
