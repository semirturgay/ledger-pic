"""Vision LLM providers behind one interface (VisionModel). Nothing outside this package imports a model SDK."""

from app.core.config import Settings
from app.services.llm.base import ExtractionFailed, VisionModel
from app.services.llm.claude import ClaudeVision
from app.services.llm.gemini import GeminiVision

__all__ = ["ExtractionFailed", "VisionModel", "vision_model_from_settings"]


def vision_model_from_settings(settings: Settings) -> VisionModel:
    """The provider chosen by EXTRACTION_PROVIDER."""
    if settings.extraction_provider == "claude":
        return ClaudeVision.from_settings(settings)
    return GeminiVision.from_settings(settings)
