import json
import re
import time
import logging
from google import genai
from app.core.config import get_settings
from app.services.providers.base import AbstractAIProvider, AIEvaluationResult

settings = get_settings()
logger = logging.getLogger("ielts.gemini")

WRITING_PROMPT = """You are an IELTS examiner. Evaluate this essay using official band descriptors (0-9, 0.5 increments).

Evaluate 4 criteria:
1. Task Response (TR)
2. Coherence and Cohesion (CC)
3. Lexical Resource (LR)
4. Grammatical Range and Accuracy (GRA)

Return ONLY valid JSON:
{
  "overall_band": 6.5,
  "criteria_scores": {
    "task_response": {"score": 6.0, "comment": "..."},
    "coherence_and_cohesion": {"score": 6.5, "comment": "..."},
    "lexical_resource": {"score": 7.0, "comment": "..."},
    "grammatical_range_and_accuracy": {"score": 6.5, "comment": "..."}
  },
  "general_feedback": "2-3 sentence high-level overall assessment. Be general, no specific corrections or word suggestions.",
  "detailed_feedback": "Comprehensive detailed assessment with specific improvement suggestions, paragraph analysis, and vocabulary alternatives...",
  "grammar_corrections": [
    {"original": "...", "corrected": "...", "explanation": "..."}
  ]
}
general_feedback must be general and brief (max 3 sentences). detailed_feedback must be comprehensive.
Be strict and objective. Do not inflate scores."""

WRITING_PROMPT_CONCISE = """You are an IELTS examiner. Evaluate this essay using official band descriptors (0-9, 0.5 increments).

Evaluate 4 criteria:
1. Task Response (TR)
2. Coherence and Cohesion (CC)
3. Lexical Resource (LR)
4. Grammatical Range and Accuracy (GRA)

Return ONLY valid JSON with CONCISE comments (max 1 short sentence each):
{
  "overall_band": 6.5,
  "criteria_scores": {
    "task_response": {"score": 6.0, "comment": "Addresses the task adequately."},
    "coherence_and_cohesion": {"score": 6.5, "comment": "Ideas are logically organized."},
    "lexical_resource": {"score": 7.0, "comment": "Good range of vocabulary."},
    "grammatical_range_and_accuracy": {"score": 6.5, "comment": "Mostly accurate with some errors."}
  },
  "general_feedback": "2-3 sentence high-level overall assessment. Be extremely general — no specific corrections, no word alternatives, no explicit error mentions.",
  "grammar_corrections": [
    {"original": "she go", "corrected": "she goes", "explanation": "Subject-verb agreement"}
  ]
}
Limit grammar_corrections to the 2 most important errors only. Be strict and objective."""

SPEAKING_PROMPT = """You are an IELTS Speaking examiner. Evaluate this transcript using official band descriptors (0-9, 0.5 increments).

Evaluate 4 criteria:
1. Fluency and Coherence (FC)
2. Lexical Resource (LR)
3. Grammatical Range and Accuracy (GRA)
4. Pronunciation (P) - infer from transcription patterns

Return ONLY valid JSON:
{
  "overall_band": 6.0,
  "criteria_scores": {
    "fluency_and_coherence": {"score": 6.0, "comment": "..."},
    "lexical_resource": {"score": 6.0, "comment": "..."},
    "grammatical_range_and_accuracy": {"score": 5.5, "comment": "..."},
    "pronunciation": {"score": 6.0, "comment": "Inferred from transcription..."}
  },
  "general_feedback": "2-3 sentence high-level speaking assessment. Be general, no specific corrections.",
  "detailed_feedback": "Comprehensive speaking assessment with specific improvement suggestions...",
  "grammar_corrections": [
    {"original": "...", "corrected": "...", "explanation": "..."}
  ]
}
general_feedback must be general and brief (max 3 sentences). detailed_feedback must be comprehensive.
Be strict and objective."""

SPEAKING_PROMPT_CONCISE = """You are an IELTS Speaking examiner. Evaluate this transcript using official band descriptors (0-9, 0.5 increments).

Evaluate 4 criteria:
1. Fluency and Coherence (FC)
2. Lexical Resource (LR)
3. Grammatical Range and Accuracy (GRA)
4. Pronunciation (P) - infer from transcription patterns

Return ONLY valid JSON with CONCISE comments (max 1 short sentence each):
{
  "overall_band": 6.0,
  "criteria_scores": {
    "fluency_and_coherence": {"score": 6.0, "comment": "Speech flows with some hesitation."},
    "lexical_resource": {"score": 6.0, "comment": "Adequate vocabulary range."},
    "grammatical_range_and_accuracy": {"score": 5.5, "comment": "Some grammar errors present."},
    "pronunciation": {"score": 6.0, "comment": "Generally clear from transcription."}
  },
  "general_feedback": "2-3 sentence high-level speaking assessment. Be extremely general — no specific corrections, no explicit error mentions.",
  "grammar_corrections": [
    {"original": "I goes", "corrected": "I go", "explanation": "Subject-verb agreement"}
  ]
}
Limit grammar_corrections to the 2 most important errors only. Be strict and objective."""


class GeminiProvider(AbstractAIProvider):

    MAX_RETRIES = 2

    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key)

    @property
    def provider_name(self) -> str:
        return "gemini"

    def _parse_json(self, raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        try:
            return self._extract_partial_json(cleaned)
        except ValueError:
            if settings.debug:
                logger.warning(f"JSON parse failed. Raw response:\n{raw[:500]}")
            raise ValueError(f"AI returned invalid JSON: {raw[:200]}")

    @staticmethod
    def _extract_partial_json(raw: str) -> dict:
        result = {}
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)

        overall = re.search(r'"overall_band"\s*:\s*([\d.]+)', raw)
        if overall:
            result["overall_band"] = float(overall.group(1))

        criteria = {}
        criteria_pattern = re.findall(
            r'"(task_response|coherence_and_cohesion|lexical_resource|grammatical_range_and_accuracy'
            r'|fluency_and_coherence|pronunciation)"\s*:\s*\{[^}]*"score"\s*:\s*([\d.]+)[^}]*\}',
            raw, re.DOTALL
        )
        for name, score in criteria_pattern:
            comment_match = re.search(
                rf'"{name}"\s*:\s*\{{[^}}]*"comment"\s*:\s*"([^"]*(?:\\.[^"]*)*)"',
                raw, re.DOTALL
            )
            comment = comment_match.group(1) if comment_match else ""
            criteria[name] = {"score": float(score), "comment": comment}

        if criteria:
            result["criteria_scores"] = criteria

        feedback_match = re.search(r'"detailed_feedback"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', raw)
        result["detailed_feedback"] = feedback_match.group(1) if feedback_match else ""

        general_match = re.search(r'"general_feedback"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', raw)
        result["general_feedback"] = general_match.group(1) if general_match else ""

        corrections = []
        corr_matches = re.findall(r'\{(?:[^{}]|\{[^{}]*\})*\}', raw)
        for match in corr_matches:
            orig = re.search(r'"original"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', match)
            corr = re.search(r'"corrected"\s*:\s*"([^"]*(?:\\.[^"]*)*)"', match)
            if orig and corr:
                corrections.append({
                    "original": orig.group(1),
                    "corrected": corr.group(1),
                    "explanation": "",
                })
        result["grammar_corrections"] = corrections

        if not result.get("overall_band"):
            raise ValueError("Could not extract overall_band from JSON")
        return result

    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        task_label = "Task 1 (Report/Letter)" if task_type == "task1" else "Task 2 (Essay)"

        prompt = WRITING_PROMPT if detailed else WRITING_PROMPT_CONCISE
        max_tokens = 8192 if detailed else 2048

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
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
                result = self._parse_json(response.text)
                return AIEvaluationResult(
                    overall_band=result["overall_band"],
                    criteria_scores=result["criteria_scores"],
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
                    logger.warning(f"Gemini attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)

        raise last_error if last_error else Exception("Gemini evaluation failed")

    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        content_type = self._get_mime_type(filename)

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                {"role": "user", "parts": [
                    {"text": "Transcribe the following audio exactly as spoken. Return ONLY the transcription, no preamble."},
                    {"inline_data": {"mime_type": content_type, "data": audio_bytes}},
                ]},
            ],
        )

        return response.text.strip()

    async def evaluate_speaking(self, transcription: str, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()

        prompt = SPEAKING_PROMPT if detailed else SPEAKING_PROMPT_CONCISE
        max_tokens = 8192 if detailed else 2048

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        {"role": "user", "parts": [{"text": prompt}]},
                        {"role": "user", "parts": [{"text": f"IELTS Speaking Response Transcript:\n{transcription}"}]},
                    ],
                    config={
                        "temperature": 0.3,
                        "max_output_tokens": max_tokens,
                        "response_mime_type": "application/json",
                    },
                )
                elapsed = int((time.time() - start) * 1000)
                result = self._parse_json(response.text)
                return AIEvaluationResult(
                    overall_band=result["overall_band"],
                    criteria_scores=result["criteria_scores"],
                    general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
                    detailed_feedback=result.get("detailed_feedback", ""),
                    grammar_corrections=result.get("grammar_corrections", []),
                    transcription=transcription,
                    model="gemini-2.5-flash",
                    tokens=response.usage_metadata.total_token_count if response.usage_metadata else 0,
                    processing_time_ms=elapsed,
                )
            except Exception as e:
                last_error = e
                if settings.debug:
                    logger.warning(f"Gemini speaking attempt {attempt + 1}/{self.MAX_RETRIES} failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(1)

        raise last_error if last_error else Exception("Gemini speaking evaluation failed")

    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        prompt = (
            "You are an IELTS Reading examiner. The student has submitted answers to reading questions. "
            "Evaluate their answers and return ONLY valid JSON:\n"
            '{"overall_band": 6.0, "criteria_scores": {"accuracy": {"score": 6.0, "comment": "..."}}, '
            '"general_feedback": "2-3 sentence overall assessment. Be general.", '
            '"detailed_feedback": "Detailed analysis of performance per question type and improvement suggestions.", '
            '"grammar_corrections": []}\n'
            "overall_band should reflect estimated IELTS band based on answer quality. Be strict and objective."
        )
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    {"role": "user", "parts": [{"text": prompt}]},
                    {"role": "user", "parts": [{"text": f"Student answers: {answers}"}]},
                ],
                config={"temperature": 0.3, "max_output_tokens": 4096, "response_mime_type": "application/json"},
            )
            elapsed = int((time.time() - start) * 1000)
            result = self._parse_json(response.text)
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

    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        prompt = (
            "You are an IELTS Listening examiner. Evaluate submitted answers from a listening test. "
            "Return ONLY valid JSON: "
            '{"overall_band": 6.5, "criteria_scores": {"accuracy": {"score": 6.5, "comment": "..."}}, '
            '"general_feedback": "2-3 sentence overall assessment.", '
            '"detailed_feedback": "Detailed analysis with improvement suggestions.", '
            '"grammar_corrections": []}\nBe strict and objective.'
        )
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"role": "user", "parts": [{"text": prompt}]}, {"role": "user", "parts": [{"text": f"Student answers: {answers}"}]}],
                config={"temperature": 0.3, "max_output_tokens": 4096, "response_mime_type": "application/json"},
            )
            elapsed = int((time.time() - start) * 1000)
            result = self._parse_json(response.text)
            return AIEvaluationResult(overall_band=result["overall_band"], criteria_scores=result.get("criteria_scores", {}), general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")), detailed_feedback=result.get("detailed_feedback", ""), grammar_corrections=[], model="gemini-2.5-flash", tokens=response.usage_metadata.total_token_count if response.usage_metadata else 0, processing_time_ms=elapsed)
        except Exception as e:
            if settings.debug: logger.warning(f"Gemini listening evaluation failed: {e}")
            raise

    @staticmethod
    def _get_mime_type(filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
        return {
            "webm": "audio/webm",
            "mp3": "audio/mpeg",
            "mp4": "audio/mp4",
            "m4a": "audio/mp4",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
        }.get(ext, "audio/webm")
