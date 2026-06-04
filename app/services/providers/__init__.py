# ============================================================
# Provider factory — returns provider instance by name
# ============================================================

from app.services.providers.base import WritingEvaluator, SpeakingEvaluator, ReadingEvaluator, ListeningEvaluator
from app.services.providers.openai import OpenAIProvider
from app.services.providers.gemini import GeminiProvider

_providers = {
    "openai": OpenAIProvider(),
    "gemini": GeminiProvider(),
}


def get_provider(name: str) -> OpenAIProvider | GeminiProvider:
    """Return the provider instance for the given name."""
    if name not in _providers:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_providers.keys())}")
    return _providers[name]
