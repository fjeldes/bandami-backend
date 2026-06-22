# ============================================================
# Provider factory — returns provider instance by name
# ============================================================

from app.services.providers.base import WritingEvaluator, SpeakingEvaluator, ReadingEvaluator, ListeningEvaluator
from app.services.providers.openai import OpenAIProvider
from app.services.providers.gemini import GeminiProvider
from app.services.providers.groq_provider import GroqProvider

_providers = {
    "openai": OpenAIProvider(),
    "gemini": GeminiProvider(),
    "groq": GroqProvider(),
}


def get_provider(name: str) -> OpenAIProvider | GeminiProvider | GroqProvider:
    """Return the provider instance for the given name."""
    if name not in _providers:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_providers.keys())}")
    return _providers[name]
