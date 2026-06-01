import json
import time
from openai import AsyncOpenAI
from app.core.config import get_settings
from app.services.providers.base import AbstractAIProvider, AIEvaluationResult

settings = get_settings()

WRITING_PROMPT = """You are an official IELTS examiner. Evaluate the following essay according to the official IELTS Writing band descriptors.

Evaluate based on these 4 criteria (each scored 0-9 in 0.5 increments):
1. Task Response (TR): How well the candidate addresses all parts of the task, presents a clear position, and supports ideas
2. Coherence and Cohesion (CC): Logical organization, clear progression, paragraphing, cohesive devices used appropriately
3. Lexical Resource (LR): Range, accuracy, and appropriateness of vocabulary; natural collocations and less common lexis
4. Grammatical Range and Accuracy (GRA): Range and accuracy of grammatical structures; error-free sentences frequency

Return ONLY valid JSON:
{
  "overall_band": 6.5,
  "criteria_scores": {
    "task_response": {"score": 6.0, "comment": "Detailed analysis of task response..."},
    "coherence_and_cohesion": {"score": 6.5, "comment": "Detailed analysis of organization..."},
    "lexical_resource": {"score": 7.0, "comment": "Detailed analysis of vocabulary..."},
    "grammatical_range_and_accuracy": {"score": 6.5, "comment": "Detailed analysis of grammar..."}
  },
  "general_feedback": "2-3 sentence high-level overall assessment. Be general, no specific corrections or word suggestions.",
  "detailed_feedback": "Comprehensive overall assessment with specific improvement suggestions...",
  "grammar_corrections": [
    {"original": "the exact error from the text", "corrected": "the corrected version", "explanation": "why this correction improves the text"}
  ]
}
general_feedback must be general and brief (max 3 sentences). detailed_feedback must be comprehensive.
Be strict, precise, and follow IELTS official descriptors. Do not inflate scores. Support every score with evidence from the text."""

SPEAKING_PROMPT = """You are an official IELTS Speaking examiner. Evaluate this transcript according to official band descriptors.

Evaluate based on 4 criteria (each 0-9, 0.5 increments):
1. Fluency and Coherence (FC): Speech rate, hesitation, logical connectors, topic development
2. Lexical Resource (LR): Vocabulary range, paraphrasing, idiomatic language, precision
3. Grammatical Range and Accuracy (GRA): Sentence complexity, error frequency, tense control
4. Pronunciation (P): Inferred from transcription patterns, word stress, intonation indicators

Return ONLY valid JSON:
{
  "overall_band": 6.0,
  "criteria_scores": {
    "fluency_and_coherence": {"score": 6.0, "comment": "Analysis of speech flow..."},
    "lexical_resource": {"score": 6.0, "comment": "Analysis of vocabulary..."},
    "grammatical_range_and_accuracy": {"score": 5.5, "comment": "Analysis of grammar..."},
    "pronunciation": {"score": 6.0, "comment": "Inferred from transcription patterns..."}
  },
  "general_feedback": "2-3 sentence high-level speaking assessment. Be general, no specific corrections.",
  "detailed_feedback": "Comprehensive speaking assessment with improvement roadmap...",
  "grammar_corrections": [
    {"original": "exact spoken error transcribed", "corrected": "corrected form", "explanation": "grammar rule or improvement rationale"}
  ]
}
general_feedback must be general and brief (max 3 sentences). detailed_feedback must be comprehensive.
Be strict and objective. Use official IELTS Speaking descriptors."""


class OpenAIProvider(AbstractAIProvider):

    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    @property
    def provider_name(self) -> str:
        return "openai"

    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        task_label = "Task 1 (Report/Letter)" if task_type == "task1" else "Task 2 (Essay)"

        response = await self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": WRITING_PROMPT},
                {"role": "user", "content": f"IELTS Writing {task_label}\n\nEssay:\n{text}"},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
            max_tokens=2000,
        )

        elapsed = int((time.time() - start) * 1000)
        result = json.loads(response.choices[0].message.content)

        return AIEvaluationResult(
            overall_band=result["overall_band"],
            criteria_scores=result["criteria_scores"],
            general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
            detailed_feedback=result["detailed_feedback"],
            grammar_corrections=result.get("grammar_corrections", []),
            model=response.model,
            tokens=response.usage.total_tokens,
            processing_time_ms=elapsed,
        )

    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        response = await self.client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, audio_bytes, self._get_mime_type(filename)),
            language="en",
        )
        return response.text

    async def evaluate_speaking(self, transcription: str, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()

        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SPEAKING_PROMPT},
                {"role": "user", "content": f"IELTS Speaking Response Transcript:\n{transcription}"},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
            max_tokens=2000,
        )

        elapsed = int((time.time() - start) * 1000)
        result = json.loads(response.choices[0].message.content)
        result["transcription"] = transcription

        return AIEvaluationResult(
            overall_band=result["overall_band"],
            criteria_scores=result["criteria_scores"],
            general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")),
            detailed_feedback=result["detailed_feedback"],
            grammar_corrections=result.get("grammar_corrections", []),
            transcription=transcription,
            model=response.model,
            tokens=response.usage.total_tokens,
            processing_time_ms=elapsed,
        )

    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        response = await self.client.chat.completions.create(
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

    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        start = time.time()
        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an IELTS Listening examiner. Evaluate answers and return JSON: {\"overall_band\": 6.5, \"criteria_scores\": {\"accuracy\": {\"score\": 6.5, \"comment\": \"...\"}}, \"general_feedback\": \"2-3 sentence assessment.\", \"detailed_feedback\": \"Detailed analysis.\", \"grammar_corrections\": []}"},
                {"role": "user", "content": f"Student answers: {answers}"},
            ],
            temperature=0.3, response_format={"type": "json_object"}, max_tokens=2000,
        )
        elapsed = int((time.time() - start) * 1000)
        result = json.loads(response.choices[0].message.content)
        return AIEvaluationResult(overall_band=result["overall_band"], criteria_scores=result.get("criteria_scores", {}), general_feedback=result.get("general_feedback", result.get("detailed_feedback", "")), detailed_feedback=result["detailed_feedback"], grammar_corrections=[], model=response.model, tokens=response.usage.total_tokens, processing_time_ms=elapsed)

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
