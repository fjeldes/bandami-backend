"""
Groq Provider — Free-tier fallback (30 req/min, 1000 req/day).
LPU-accelerated inference, OpenAI-compatible API.
Model: llama-3.3-70b-versatile
"""
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.services.providers.openai import OpenAIProvider


class GroqProvider(OpenAIProvider):
    def _get_client(self):
        if self._client is None:
            s = get_settings()
            if not s.groq_api_key:
                raise ValueError("GROQ_API_KEY not configured")
            self._client = AsyncOpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=s.groq_api_key.strip(),
            )
        return self._client

    @property
    def provider_name(self) -> str:
        return "groq"

    async def _call_ai(self, prompt: str, transcription: str, max_tokens: int, temperature: float):
        return await self._get_client().chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"IELTS Speaking Response:\n{transcription}"},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        response = await self._get_client().audio.transcriptions.create(
            model="whisper-large-v3",
            file=(filename, audio_bytes, self._get_mime_type(filename)),
            language="en",
        )
        return response.text
