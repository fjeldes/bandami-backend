from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AIEvaluationResult:
    overall_band: float
    criteria_scores: dict
    general_feedback: str = ""
    detailed_feedback: str = ""
    grammar_corrections: list[dict] = None
    transcription: str | None = None
    model: str = ""
    tokens: int = 0
    processing_time_ms: int = 0

    def __post_init__(self):
        if self.grammar_corrections is None:
            self.grammar_corrections = []


class AbstractAIProvider(ABC):

    @abstractmethod
    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult:
        ...

    @abstractmethod
    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        ...

    @abstractmethod
    async def evaluate_speaking(self, transcription: str, detailed: bool = True) -> AIEvaluationResult:
        ...

    @abstractmethod
    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        ...

    @abstractmethod
    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...
