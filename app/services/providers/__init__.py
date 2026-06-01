from app.services.providers.base import AbstractAIProvider
from app.services.providers.gemini import GeminiProvider
from app.services.providers.openai import OpenAIProvider

_gemini = GeminiProvider()
_openai = OpenAIProvider()

PROVIDER_MAP: dict[str, AbstractAIProvider] = {
    "gemini": _gemini,
    "openai": _openai,
}


def get_provider(provider_name: str) -> AbstractAIProvider:
    provider = PROVIDER_MAP.get(provider_name)
    if provider is None:
        raise ValueError(f"Unknown AI provider: {provider_name}. Available: {list(PROVIDER_MAP.keys())}")
    return provider
