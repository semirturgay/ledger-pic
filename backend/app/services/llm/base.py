"""What the receipt extractor needs from a vision LLM, and how a provider reports that it can't be used."""

from typing import Any, Protocol

# Room for the largest valid answer (200 line items); providers cap the model's output here.
MAX_OUTPUT_TOKENS = 8192

class ExtractionFailed(Exception):
    """The model couldn't be used. `retryable`: may succeed later (rate limit, outage, network);
    `retry_after`: seconds the service asked us to wait, if it said; `timed_out`: the call ran out of
    time, which the image itself can cause (a very long answer), so it is retried only once."""

    def __init__(self, message: str, *, retryable: bool, retry_after: float | None = None, timed_out: bool = False) -> None:
        super().__init__(message)
        self.retryable, self.retry_after, self.timed_out = retryable, retry_after, timed_out

    # The failures every provider maps its own SDK errors to.
    @classmethod
    def rate_limited(cls, retry_after: float | None) -> "ExtractionFailed":
        return cls("The AI service's rate limit was reached.", retryable=True, retry_after=retry_after)

    @classmethod
    def busy(cls) -> "ExtractionFailed":
        return cls("The AI service is busy.", retryable=True)

    @classmethod
    def unreachable(cls) -> "ExtractionFailed":
        return cls("Couldn't reach the AI service.", retryable=True)

    @classmethod
    def took_too_long(cls) -> "ExtractionFailed":
        return cls("The AI took too long to read this image.", retryable=True, timed_out=True)

    @classmethod
    def rejected(cls, status: int | None, provider: str) -> "ExtractionFailed":
        return cls(f"The AI service rejected the request ({status}). Check the server's {provider} settings.", retryable=False)


class VisionModel(Protocol):
    """One vision LLM. `read` sends an image and a prompt, asks for JSON matching `schema`, and returns the
    model's raw text: it may still be invalid, the caller validates it. It raises ExtractionFailed when the
    model can't be used. `name` is recorded on each receipt, so every draft can be traced to its model."""

    name: str

    async def read(self, image: bytes, mime_type: str, prompt: str, schema: dict[str, Any]) -> str: ...


class Unconfigured:
    """Stands in when the chosen provider has no API key: every call fails, clearly and without retries."""

    def __init__(self, name: str, message: str) -> None:
        self.name, self._message = name, message

    async def read(self, _image: bytes, _mime_type: str, _prompt: str, _schema: dict[str, Any]) -> str:
        raise ExtractionFailed(self._message, retryable=False)
