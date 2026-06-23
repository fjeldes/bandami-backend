# ============================================================
# OpenAI Provider (SOLID refactor)
# Implements focused protocols, uses extracted prompts + JsonParser.
# ============================================================

import json
import time
import asyncio
from app.services.privacy import sanitize_for_ai
import logging
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.services.providers.base import (
    AIEvaluationResult,
    WritingEvaluator,
    BaseSpeakingEvaluator,
    ReadingEvaluator,
    ListeningEvaluator,
    validate_writing_criteria,
    generate_fallback_criteria,
)
from app.services.prompts.writing import WRITING_OPENAI, WRITING_PREMIUM

settings = get_settings()
logger = logging.getLogger("ielts.openai")


class OpenAIProvider(BaseSpeakingEvaluator, WritingEvaluator, ReadingEvaluator, ListeningEvaluator):

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not settings.openai_api_key:
                raise ValueError("OpenAI API key not configured")
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._client

    @property
    def provider_name(self) -> str:
        return "openai"

    # ---- Writing -----------------------------------------------------------

    MAX_RETRIES = 2

    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        text = sanitize_for_ai(text)
        is_premium = detailed
        task_label = "Task 1 (Report/Letter)" if task_type == "task1" else "Task 2 (Essay)"
        base_prompt = WRITING_PREMIUM if is_premium else WRITING_OPENAI
        max_tokens = 8192 if is_premium else 4096

        result = None
        criteria = {}
        missing = []
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self._get_client().chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": base_prompt},
                        {"role": "user", "content": f"IELTS Writing {task_label}\n\nEssay ({len(text.split())} words):\n{text}"},
                    ],
                    temperature=0.3,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                )

                elapsed = int((time.time() - start) * 1000)
                result = json.loads(response.choices[0].message.content)
                criteria = result.get("criteria_scores", {})
                missing = validate_writing_criteria(criteria, premium=is_premium)

                if not missing:
                    return AIEvaluationResult(
                        overall_band=result["overall_band"],
                        criteria_scores=criteria,
                        general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
                        detailed_feedback=result.get("detailed_feedback", "") if is_premium else result.get("detailed_feedback", result.get("general_feedback", "")),
                        grammar_corrections=result.get("grammar_corrections", []),
                        model=response.model,
                        tokens=response.usage.total_tokens,
                        processing_time_ms=elapsed,
                    )

                logger.warning(f"OpenAI writing criteria missing (attempt {attempt + 1}/{self.MAX_RETRIES}): {missing}")
                base_prompt = (
                    f"YOUR PREVIOUS RESPONSE WAS REJECTED — you omitted criteria: {', '.join(missing)}.\n\n"
                    + base_prompt
                    + "\n\nFIX YOUR MISTAKE: ALL required criteria_scores keys must be present."
                )
                max_tokens = min(max_tokens * 2, 16384)

            except Exception as e:
                last_error = e
                logger.warning(f"OpenAI writing attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(1)

        if result:
            overall_band = result.get("overall_band", 5.0)
            if missing:
                logger.error(
                    f"OpenAI criteria STILL missing after {self.MAX_RETRIES} attempts: {missing}. "
                    f"Generating fallback from overall_band={overall_band}."
                )
                fallback = generate_fallback_criteria(overall_band, premium=is_premium)
                for key in missing:
                    criteria[key] = fallback[key]
            return AIEvaluationResult(
                overall_band=overall_band,
                criteria_scores=criteria,
                general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
                detailed_feedback=result.get("detailed_feedback", "") if is_premium else result.get("detailed_feedback", result.get("general_feedback", "")),
                grammar_corrections=result.get("grammar_corrections", []),
                model=result.get("model", "gpt-4o"),
                tokens=0,
                processing_time_ms=int((time.time() - start) * 1000),
            )

        raise last_error if last_error else Exception("OpenAI writing evaluation failed")

    # ---- Speaking (template method from BaseSpeakingEvaluator) -------------

    def _get_speaking_config(self, is_premium: bool) -> tuple[str, int]:
        from app.services.prompts.speaking import SPEAKING_PREMIUM, SPEAKING_FREE
        if is_premium:
            return SPEAKING_PREMIUM, 8192
        return SPEAKING_FREE, 4096

    async def _call_ai(self, prompt: str, transcription: str, max_tokens: int, temperature: float):
        return await self._get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"IELTS Speaking Response:\n{transcription}"},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )

    def _parse_response(self, response) -> dict:
        return json.loads(response.choices[0].message.content)

    def _build_result(self, parsed: dict, criteria: dict, transcription: str, response) -> AIEvaluationResult:
        return AIEvaluationResult(
            overall_band=parsed["overall_band"],
            criteria_scores=criteria,
            general_feedback=parsed.get("general_feedback", parsed.get("detailed_feedback", "")),
            detailed_feedback=parsed.get("detailed_feedback", ""),
            grammar_corrections=parsed.get("grammar_corrections", []),
            transcription=transcription,
            model=response.model,
            tokens=response.usage.total_tokens,
            processing_time_ms=0,
        )

    # ---- Transcription -----------------------------------------------------

    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        response = await self._get_client().audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_bytes, self._get_mime_type(filename)),
            language="en",
        )
        return response.text

    # ---- Reading (stub — "Coming Soon") ------------------------------------

    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        response = await self._get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You are an IELTS Reading examiner. Evaluate submitted answers and return JSON: "
                    '{"overall_band": 6.0, "criteria_scores": {"accuracy": {"score": 6.0, "comment": "..."}}, '
                    '"general_feedback": "2-3 sentence assessment.", '
                    '"detailed_feedback": "Detailed analysis and improvement suggestions.", '
                    '"grammar_corrections": []}'
                )},
                {"role": "user", "content": f"Student answers: {answers}"},
            ],
            temperature=0.3, response_format={"type": "json_object"}, max_tokens=2000,
        )
        elapsed = int((time.time() - start) * 1000)
        result = json.loads(response.choices[0].message.content)
        return AIEvaluationResult(
            overall_band=result["overall_band"],
            criteria_scores=result.get("criteria_scores", {}),
            general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
            detailed_feedback=result["detailed_feedback"],
            grammar_corrections=result.get("grammar_corrections", []),
            model=response.model, tokens=response.usage.total_tokens, processing_time_ms=elapsed,
        )

    # ---- Listening (stub — "Coming Soon") ----------------------------------

    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        response = await self._get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You are an IELTS Listening examiner. Evaluate answers and return JSON: "
                    '{"overall_band": 6.5, "criteria_scores": {"accuracy": {"score": 6.5, "comment": "..."}}, '
                    '"general_feedback": "2-3 sentence assessment.", "detailed_feedback": "Detailed analysis.", '
                    '"grammar_corrections": []}'
                )},
                {"role": "user", "content": f"Student answers: {answers}"},
            ],
            temperature=0.3, response_format={"type": "json_object"}, max_tokens=2000,
        )
        elapsed = int((time.time() - start) * 1000)
        result = json.loads(response.choices[0].message.content)
        return AIEvaluationResult(
            overall_band=result["overall_band"],
            criteria_scores=result.get("criteria_scores", {}),
            general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
            detailed_feedback=result["detailed_feedback"],
            grammar_corrections=[],
            model=response.model, tokens=response.usage.total_tokens, processing_time_ms=elapsed,
        )

    @staticmethod
    def _get_mime_type(filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
        return {
            "webm": "audio/webm", "mp3": "audio/mpeg", "mp4": "audio/mp4",
            "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg",
        }.get(ext, "audio/webm")
