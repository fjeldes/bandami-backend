# ============================================================
# Gemini Provider (SOLID refactor)
# Implements focused protocols, uses extracted prompts + JsonParser.
# ============================================================

import time
import asyncio
import logging
from google import genai
from app.core.config import get_settings
from app.services.providers.base import (
    AIEvaluationResult,
    WritingEvaluator,
    BaseSpeakingEvaluator,
    ReadingEvaluator,
    ListeningEvaluator,
    validate_writing_criteria,
)
from app.services.prompts.writing import WRITING_DETAILED, WRITING_CONCISE, WRITING_PREMIUM
from app.services.parsing.json_parser import JsonParser

settings = get_settings()
logger = logging.getLogger("ielts.gemini")


class GeminiProvider(BaseSpeakingEvaluator, WritingEvaluator, ReadingEvaluator, ListeningEvaluator):

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            if not settings.gemini_api_key:
                raise ValueError("Gemini API key not configured")
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    @property
    def provider_name(self) -> str:
        return "gemini"

    # ---- Writing -----------------------------------------------------------

    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        is_premium = detailed
        task_label = "Task 1 (Report/Letter)" if task_type == "task1" else "Task 2 (Essay)"
        prompt = WRITING_PREMIUM if is_premium else (WRITING_DETAILED if detailed else WRITING_CONCISE)
        max_tokens = 8192 if is_premium else (8192 if detailed else 2048)

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self._get_client().models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        {"role": "user", "parts": [{"text": prompt}]},
                        {"role": "user", "parts": [{"text": f"IELTS Writing {task_label}\n\nEssay:\n{text}"}]},
                    ],
                    config={
                        "temperature": 0.3,
                        "max_output_tokens": max_tokens,
                        "response_mime_type": "application/json",
                    },
                )
                elapsed = int((time.time() - start) * 1000)
                result = JsonParser.parse(response.text)
                criteria = result.get("criteria_scores", {})
                if is_premium:
                    missing = validate_writing_criteria(criteria, premium=True)
                    if missing:
                        logger.warning(f"Writing criteria missing: {missing}")
                return AIEvaluationResult(
                    overall_band=result["overall_band"],
                    criteria_scores=criteria,
                    general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
                    detailed_feedback=result.get("detailed_feedback", ""),
                    grammar_corrections=result.get("grammar_corrections", []),
                    model="gemini-2.5-flash",
                    tokens=response.usage_metadata.total_token_count if response.usage_metadata else 0,
                    processing_time_ms=elapsed,
                )
            except Exception as e:
                last_error = e
                if settings.debug:
                    logger.warning(f"Gemini writing attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)

        raise last_error if last_error else Exception("Gemini writing evaluation failed")

    # ---- Speaking (template method from BaseSpeakingEvaluator) -------------

    def _get_speaking_config(self, is_premium: bool) -> tuple[str, int]:
        from app.services.prompts.speaking import SPEAKING_PREMIUM, SPEAKING_CONCISE
        if is_premium:
            return SPEAKING_PREMIUM, 8192
        return SPEAKING_CONCISE, 4096

    async def _call_ai(self, prompt: str, transcription: str, max_tokens: int, temperature: float):
        return self._get_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                {"role": "user", "parts": [{"text": prompt}]},
                {"role": "user", "parts": [{"text": f"IELTS Speaking Response:\n{transcription}"}]},
            ],
            config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "response_mime_type": "application/json",
            },
        )

    def _parse_response(self, response) -> dict:
        return JsonParser.parse(response.text)

    def _build_result(self, parsed: dict, criteria: dict, transcription: str, response) -> AIEvaluationResult:
        return AIEvaluationResult(
            overall_band=parsed["overall_band"],
            criteria_scores=criteria,
            general_feedback=parsed.get("general_feedback", parsed.get("detailed_feedback", "")),
            detailed_feedback=parsed.get("detailed_feedback", ""),
            grammar_corrections=parsed.get("grammar_corrections", []),
            transcription=transcription,
            model="gemini-2.5-flash",
            tokens=response.usage_metadata.total_token_count if response.usage_metadata else 0,
            processing_time_ms=0,
        )

    # ---- Transcription -----------------------------------------------------

    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        content_type = self._get_mime_type(filename)
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = self._get_client().models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        {"role": "user", "parts": [
                            {"text": "Transcribe the following audio exactly as spoken. Return ONLY the transcription, no preamble."},
                            {"inline_data": {"mime_type": content_type, "data": audio_bytes}},
                        ]},
                    ],
                )
                return response.text.strip()
            except Exception as e:
                last_error = e
                if settings.debug:
                    logger.warning(f"Gemini transcription attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)

        raise last_error if last_error else Exception("Gemini transcription failed after all retries")

    # ---- Reading (stub — "Coming Soon") ------------------------------------

    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        prompt = (
            "You are an IELTS Reading examiner. Evaluate submitted answers and return ONLY valid JSON:\n"
            '{"overall_band": 6.0, "criteria_scores": {"accuracy": {"score": 6.0, "comment": "..."}}, '
            '"general_feedback": "2-3 sentence assessment.", '
            '"detailed_feedback": "Detailed analysis and improvement suggestions.", '
            '"grammar_corrections": []}\nBe strict and objective.'
        )
        try:
            response = self._get_client().models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {"role": "user", "parts": [{"text": prompt}]},
                    {"role": "user", "parts": [{"text": f"Student answers: {answers}"}]},
                ],
                config={"temperature": 0.3, "max_output_tokens": 4096, "response_mime_type": "application/json"},
            )
            elapsed = int((time.time() - start) * 1000)
            result = JsonParser.parse(response.text)
            return AIEvaluationResult(
                overall_band=result["overall_band"],
                criteria_scores=result.get("criteria_scores", {}),
                general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
                detailed_feedback=result.get("detailed_feedback", ""),
                grammar_corrections=result.get("grammar_corrections", []),
                model="gemini-2.5-flash",
                tokens=response.usage_metadata.total_token_count if response.usage_metadata else 0,
                processing_time_ms=elapsed,
            )
        except Exception as e:
            if settings.debug:
                logger.warning(f"Gemini reading evaluation failed: {e}")
            raise

    # ---- Listening (stub — "Coming Soon") ----------------------------------

    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        prompt = (
            "You are an IELTS Listening examiner. Evaluate submitted answers. Return ONLY valid JSON: "
            '{"overall_band": 6.5, "criteria_scores": {"accuracy": {"score": 6.5, "comment": "..."}}, '
            '"general_feedback": "2-3 sentence assessment.", '
            '"detailed_feedback": "Detailed analysis with improvement suggestions.", '
            '"grammar_corrections": []}\nBe strict and objective.'
        )
        try:
            response = self._get_client().models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {"role": "user", "parts": [{"text": prompt}]},
                    {"role": "user", "parts": [{"text": f"Student answers: {answers}"}]},
                ],
                config={"temperature": 0.3, "max_output_tokens": 4096, "response_mime_type": "application/json"},
            )
            elapsed = int((time.time() - start) * 1000)
            result = JsonParser.parse(response.text)
            return AIEvaluationResult(
                overall_band=result["overall_band"],
                criteria_scores=result.get("criteria_scores", {}),
                general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
                detailed_feedback=result.get("detailed_feedback", ""),
                grammar_corrections=[],
                model="gemini-2.5-flash",
                tokens=response.usage_metadata.total_token_count if response.usage_metadata else 0,
                processing_time_ms=elapsed,
            )
        except Exception as e:
            if settings.debug:
                logger.warning(f"Gemini listening evaluation failed: {e}")
            raise

    @staticmethod
    def _get_mime_type(filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
        return {
            "webm": "audio/webm", "mp3": "audio/mpeg", "mp4": "audio/mp4",
            "m4a": "audio/mp4", "wav": "audio/wav", "ogg": "audio/ogg",
        }.get(ext, "audio/webm")
