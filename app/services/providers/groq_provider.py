"""
Groq Provider — Free-tier fallback (30 req/min, 1000 req/day).
LPU-accelerated inference, OpenAI-compatible API.
Model: llama-3.3-70b-versatile
"""
import os
from openai import OpenAI
from app.services.providers.openai import OpenAIProvider


class GroqProvider(OpenAIProvider):
    def __init__(self):
        super().__init__()
        self._client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ.get("GROQ_API_KEY", ""),
        )

    @property
    def provider_name(self) -> str:
        return "groq"
