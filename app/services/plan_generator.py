import json
import logging

logger = logging.getLogger("ielts.plan_generator")


async def generate_plan(prompt: str) -> dict:
    """Generate a study plan using Gemini (primary) or OpenAI (fallback).
    Returns parsed JSON dict matching: {"plan": [...], "message": "..."}
    """
    from app.core.config import get_settings
    settings = get_settings()
    errors = []

    # Try Gemini first
    if settings.gemini_api_key:
        try:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                config={"temperature": 0.7, "max_output_tokens": 2048, "response_mime_type": "application/json"},
            )
            raw = response.text.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Gemini plan generation failed: {e}")
            errors.append(f"gemini: {e}")

    # Fallback to OpenAI
    if settings.openai_api_key:
        try:
            from openai import AsyncOpenAI
            import asyncio
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are an IELTS study coach. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                response_format={"type": "json_object"},
                max_tokens=2048,
            )
            raw = response.choices[0].message.content.strip()
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"OpenAI plan generation failed: {e}")
            errors.append(f"openai: {e}")

    raise RuntimeError(f"All providers failed to generate plan: {'; '.join(errors)}")
