from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from uuid import UUID


# ---- Enums ----
ExamType = Literal["writing", "speaking"]
ExamStatus = Literal["pending", "processing", "completed", "failed"]
WritingTask = Literal["task1", "task2"]
AIProvider = Literal["gemini", "openai"]
SubscriptionTier = Literal["free", "premium"]
EvalSource = Literal["daily", "credit_pack", "pro_monthly", "free"]


# ---- User ----
class UserProfile(BaseModel):
    id: UUID
    email: str
    full_name: Optional[str] = None
    subscription_tier: SubscriptionTier = "free"
    stripe_customer_id: Optional[str] = None
    created_at: datetime


# ---- Question ----
class QuestionResponse(BaseModel):
    id: UUID
    exam_type: ExamType
    task_type: Optional[WritingTask] = None
    difficulty: int
    prompt_text: str
    title: Optional[str] = None
    module: Optional[str] = None


# ---- Exam ----
class ExamCreate(BaseModel):
    exam_type: ExamType
    task_type: Optional[WritingTask] = None
    question_id: Optional[UUID] = None
    attempt_number: int = 1
    eval_source: EvalSource = "daily"


class ExamResponse(BaseModel):
    id: UUID
    user_id: UUID
    question_id: Optional[UUID] = None
    exam_type: ExamType
    task_type: Optional[WritingTask] = None
    status: ExamStatus
    attempt_number: int = 1
    time_taken_seconds: Optional[int] = None
    eval_source: EvalSource = "daily"
    created_at: datetime
    completed_at: Optional[datetime] = None


# ---- Grammar Correction ----
class GrammarCorrection(BaseModel):
    original: str
    corrected: str
    explanation: str


class CriterionScore(BaseModel):
    score: float = Field(..., ge=0, le=9)
    comment: str


# ---- Writing ----
class WritingSubmission(BaseModel):
    exam_id: UUID
    text: str = Field(..., min_length=50, max_length=5000)


class WritingCriteriaScores(BaseModel):
    task_response: CriterionScore
    coherence_and_cohesion: CriterionScore
    lexical_resource: CriterionScore
    grammatical_range_and_accuracy: CriterionScore


# ---- Speaking ----
class SpeakingCriteriaScores(BaseModel):
    fluency_and_coherence: CriterionScore
    lexical_resource: CriterionScore
    grammatical_range_and_accuracy: CriterionScore
    pronunciation: CriterionScore


# ---- Evaluation (respuesta al frontend) ----
class EvaluationResponse(BaseModel):
    id: UUID
    exam_id: UUID
    user_submission: str
    prompt_text: Optional[str] = None
    overall_band: Optional[float] = None
    criteria_scores: dict
    general_feedback: Optional[str] = None
    detailed_feedback: Optional[str] = None
    grammar_corrections: list[GrammarCorrection] = []
    provider_used: AIProvider = "gemini"
    ai_model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    processing_time_ms: Optional[int] = None
    feedback_unlocks_at: datetime
    is_feedback_visible: bool = True
    upgraded_text: Optional[str] = None
    created_at: datetime


# ---- Dashboard ----
class DashboardStats(BaseModel):
    subscription_tier: SubscriptionTier = "free"
    daily_eval_limit: int = 4
    daily_evals_used: int = 0
    daily_evals_remaining: int = 0
    total_exams: int = 0
    average_band: Optional[float] = None
    highest_band: Optional[float] = None
    writing_exams: int = 0
    speaking_exams: int = 0
    completed_exams: int = 0
    extra_credits_available: int = 0


# ---- Subscription Plan ----
class SubscriptionPlanResponse(BaseModel):
    id: UUID
    slug: str
    name: str
    description: Optional[str] = None
    price_cents: int
    currency: str
    interval: str
    daily_eval_limit: int
    provider: AIProvider
    feedback_delay_hours: int
    sort_order: int


# ---- User Subscription ----
class UserSubscriptionResponse(BaseModel):
    id: UUID
    user_id: UUID
    plan_id: UUID
    status: str
    current_period_start: datetime
    current_period_end: datetime
    auto_renew: bool


# ---- Credit Pack ----
class UserCreditPackResponse(BaseModel):
    id: UUID
    credits_total: int
    credits_used: int
    credits_remaining: int = 0
    purchased_at: datetime
    expires_at: Optional[datetime] = None


# ---- Error ----
class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
