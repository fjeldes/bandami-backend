# ============================================================
# Base classes for AI providers (SOLID refactor)
# - 4 focused protocols (ISP)
# - BaseEvaluator template method with retry/fallback (SRP)
# - Shared helpers for validation and fallback generation
# ============================================================

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("ielts.providers.base")


class ProviderUnavailableError(Exception):
    """Raised when the AI provider is unavailable after exhausting all retries."""

SPEAKING_CRITERIA_KEYS = [
    "fluency_and_coherence",
    "lexical_resource",
    "grammatical_range_and_accuracy",
    "pronunciation",
]

SPEAKING_SUB_CRITERIA = {
    "fluency_and_coherence": [
        ("fluency", "Fluency"),
        ("coherence", "Coherence"),
    ],
    "lexical_resource": [
        ("vocabulary_range", "Vocabulary Range"),
        ("vocabulary_precision", "Vocabulary Precision"),
        ("paraphrasing", "Paraphrasing"),
    ],
    "grammatical_range_and_accuracy": [
        ("grammar_range", "Grammatical Range"),
        ("grammar_accuracy", "Grammatical Accuracy"),
    ],
    "pronunciation": [
        ("pronunciation_clarity", "Pronunciation Clarity"),
    ],
}

SPEAKING_SUB_CRITERIA_KEYS = [
    key for sublist in SPEAKING_SUB_CRITERIA.values() for key, _ in sublist
]

ALL_SPEAKING_KEYS = SPEAKING_CRITERIA_KEYS + SPEAKING_SUB_CRITERIA_KEYS

WRITING_CRITERIA_KEYS = [
    "task_response",
    "coherence_and_cohesion",
    "lexical_resource",
    "grammatical_range_and_accuracy",
]

WRITING_SUB_CRITERIA = {
    "task_response": [
        ("task_fulfillment", "Task Fulfillment"),
        ("position_clarity", "Position Clarity"),
    ],
    "coherence_and_cohesion": [
        ("paragraph_structure", "Paragraph Structure"),
        ("cohesion_devices", "Cohesion Devices"),
    ],
    "lexical_resource": [
        ("vocabulary_range", "Vocabulary Range"),
        ("vocabulary_precision", "Vocabulary Precision"),
    ],
    "grammatical_range_and_accuracy": [
        ("grammar_range", "Grammatical Range"),
        ("grammar_accuracy", "Grammatical Accuracy"),
    ],
}

WRITING_SUB_CRITERIA_KEYS = [
    key for sublist in WRITING_SUB_CRITERIA.values() for key, _ in sublist
]

ALL_WRITING_KEYS = WRITING_CRITERIA_KEYS + WRITING_SUB_CRITERIA_KEYS


@dataclass
class AIEvaluationResult:
    overall_band: float
    criteria_scores: dict
    general_feedback: str = ""
    detailed_feedback: str = ""
    grammar_corrections: list = field(default_factory=list)
    transcription: str | None = None
    model: str = ""
    tokens: int = 0
    processing_time_ms: int = 0


# ---- Focused protocols (ISP) -----------------------------------------------

class WritingEvaluator(ABC):
    @abstractmethod
    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult: ...

class SpeakingEvaluator(ABC):
    @abstractmethod
    async def evaluate_speaking(self, transcription: str, detailed: bool = True) -> AIEvaluationResult: ...
    @abstractmethod
    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str: ...

class ReadingEvaluator(ABC):
    @abstractmethod
    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult: ...

class ListeningEvaluator(ABC):
    @abstractmethod
    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult: ...


# ---- Template method for speaking retry/fallback (SRP) -------------------

class BaseSpeakingEvaluator(SpeakingEvaluator):
    """Template method: _call_ai + _parse_response are provider-specific,
       the 3-layer retry/fallback pipeline is shared."""

    MAX_RETRIES = 2

    async def evaluate_speaking(self, transcription: str, detailed: bool = True) -> AIEvaluationResult:
        import time
        from app.services.privacy import sanitize_for_ai
        start = time.time()
        transcription = sanitize_for_ai(transcription)
        is_premium = detailed

        prompt, max_tokens = self._get_speaking_config(is_premium)
        base_prompt = prompt

        result = None
        criteria = {}
        missing = []

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self._call_ai(prompt, transcription, max_tokens, temperature=0.3)
                result = self._parse_response(response)
                criteria = result.get("criteria_scores", {})
                missing = validate_speaking_criteria(criteria, premium=is_premium)

                if not missing:
                    return self._build_result(result, criteria, transcription, response)

                # Capa 2: retry with reinforced prompt
                logger.warning(
                    f"{self.provider_name} speaking criteria missing (attempt {attempt + 1}): {missing}. Retrying..."
                )
                prompt = (
                    f"YOUR PREVIOUS RESPONSE WAS REJECTED — you omitted: {', '.join(missing)}.\n\n"
                    + base_prompt
                    + "\n\nFIX YOUR MISTAKE: ensure ALL required criteria_scores keys are present."
                )
                max_tokens = max(max_tokens * 2, 16384)

            except Exception as e:
                logger.warning(f"{self.provider_name} speaking attempt {attempt + 1} failed: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    await self._sleep(1)

        # Capa 3: fallback from overall_band
        overall_band = result.get("overall_band", 5.0) if result else 5.0

        if missing:
            logger.error(
                f"{self.provider_name} criteria STILL missing after {self.MAX_RETRIES} attempts: {missing}. "
                f"Generating fallback from overall_band={overall_band}."
            )
            fallback = generate_fallback_criteria(overall_band, premium=is_premium)
            for key in missing:
                criteria[key] = fallback[key]

        if not result:
            raise ProviderUnavailableError(f"{self.provider_name} speaking evaluation failed after all retries")

        return self._build_result(result, criteria, transcription, response)

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def _get_speaking_config(self, is_premium: bool) -> tuple[str, int]:
        """Return (prompt, max_tokens) for the speaking evaluation."""

    @abstractmethod
    async def _call_ai(self, prompt: str, transcription: str, max_tokens: int, temperature: float):
        """Call the AI API and return the raw response."""

    @abstractmethod
    def _parse_response(self, response) -> dict:
        """Parse the raw AI response into a dict."""

    @abstractmethod
    def _build_result(self, parsed: dict, criteria: dict, transcription: str, response) -> AIEvaluationResult:
        """Build the final AIEvaluationResult."""

    @staticmethod
    async def _sleep(seconds: float):
        import asyncio
        await asyncio.sleep(seconds)


# ---- Helpers ---------------------------------------------------------------

def _descriptions() -> dict:
    return {
        "fluency_and_coherence": "Fluency and coherence typically align with overall speaking ability.",
        "lexical_resource": "Lexical resource often trails overall fluency by ~0.5 bands.",
        "grammatical_range_and_accuracy": "Grammatical accuracy commonly lags behind fluency by ~0.5 bands.",
        "pronunciation": "Pronunciation is estimated from overall speaking performance.",
        "fluency": "Speech rate, pauses, fillers, and hesitation patterns.",
        "coherence": "Logical connectors, topic development, and discourse structure.",
        "vocabulary_range": "Variety, less common words, and idiomatic language.",
        "vocabulary_precision": "Word choice accuracy, collocations, and appropriate register.",
        "paraphrasing": "Ability to rephrase, use synonyms, and avoid repetition.",
        "grammar_range": "Variety of sentence structures (simple, compound, complex).",
        "grammar_accuracy": "Error frequency, tense control, articles, and prepositions.",
        "pronunciation_clarity": "Clarity inferred from spoken response, word stress, and intonation patterns.",
    }


def validate_speaking_criteria(criteria: dict, premium: bool = False) -> list[str]:
    required = SPEAKING_CRITERIA_KEYS.copy()
    if premium:
        required += SPEAKING_SUB_CRITERIA_KEYS
    return [k for k in required if k not in criteria or not isinstance(criteria.get(k), dict)]


def validate_writing_criteria(criteria: dict, premium: bool = False) -> list[str]:
    required = WRITING_CRITERIA_KEYS.copy()
    if premium:
        required += WRITING_SUB_CRITERIA_KEYS
    return [k for k in required if k not in criteria or not isinstance(criteria.get(k), dict)]


def generate_fallback_criteria(overall_band: float, premium: bool = False) -> dict:
    band = clamp_band(overall_band)
    desc = _descriptions()

    main_offsets = {
        "fluency_and_coherence": 0.0,
        "lexical_resource": -0.5,
        "grammatical_range_and_accuracy": -0.5,
        "pronunciation": 0.0,
    }
    result = {}
    for key in SPEAKING_CRITERIA_KEYS:
        offset = main_offsets.get(key, 0.0)
        score = clamp_band(band + offset)
        result[key] = {
            "score": score,
            "comment": f"Estimated from overall band ({band}). {desc.get(key, '')}",
        }

    if premium:
        sub_offsets = {
            "fluency": ("fluency_and_coherence", 0.0),
            "coherence": ("fluency_and_coherence", 0.0),
            "vocabulary_range": ("lexical_resource", 0.0),
            "vocabulary_precision": ("lexical_resource", -0.5),
            "paraphrasing": ("lexical_resource", 0.0),
            "grammar_range": ("grammatical_range_and_accuracy", 0.0),
            "grammar_accuracy": ("grammatical_range_and_accuracy", -0.5),
            "pronunciation_clarity": ("pronunciation", 0.0),
        }
        for key, (parent, offset) in sub_offsets.items():
            parent_score = result.get(parent, {}).get("score", band)
            score = clamp_band(parent_score + offset)
            result[key] = {
                "score": score,
                "comment": f"Estimated from {parent.replace('_', ' ')} ({parent_score}). {desc.get(key, '')}",
            }

    logger.warning(
        f"Generated fallback criteria from overall_band={band} (premium={premium}): "
        f"{ {k: v['score'] for k, v in result.items()} }"
    )
    return result


def clamp_band(value: float) -> float:
    return round(max(0, min(9, value)) * 2) / 2
