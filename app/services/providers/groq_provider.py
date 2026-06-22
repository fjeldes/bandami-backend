"""
Groq Provider — Free-tier fallback (30 req/min, 1000 req/day).
LPU-accelerated inference, OpenAI-compatible API.
Model: llama-3.3-70b-versatile
"""
from openai import OpenAI
from app.core.config import get_settings
from app.services.providers.openai import OpenAIProvider


class GroqProvider(OpenAIProvider):
    def _get_client(self):
        if self._client is None:
            s = get_settings()
            if not s.groq_api_key:
                raise ValueError("GROQ_API_KEY not configured")
            self._client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=s.groq_api_key.strip(),
            )
        return self._client

    @property
    def provider_name(self) -> str:
        return "groq"
