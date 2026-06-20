import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, SmallInteger, Float, Boolean,
    DateTime, ForeignKey, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.engine import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Question(Base):
    __tablename__ = "question_bank"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_type = Column(String, nullable=False)  # 'writing' | 'speaking'
    task_type = Column(String, nullable=True)   # 'task1' | 'task2'
    difficulty = Column(SmallInteger, nullable=False, default=1)
    prompt_text = Column(Text, nullable=False)
    title = Column(String, nullable=True)
    module = Column(String, default="general")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class Exam(Base):
    __tablename__ = "exams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id = Column(UUID(as_uuid=True), ForeignKey("question_bank.id"), nullable=True)
    exam_type = Column(String, nullable=False)   # 'writing' | 'speaking'
    task_type = Column(String, nullable=True)    # 'task1' | 'task2'
    status = Column(String, nullable=False, default="pending")
    attempt_number = Column(Integer, nullable=False, default=1)
    time_taken_seconds = Column(Integer, nullable=True)
    eval_source = Column(String, nullable=False, default="daily")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("UserProfile", back_populates="exams")
    evaluation = relationship("Evaluation", back_populates="exam", uselist=False)
    question = relationship("Question", lazy="joined")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_id = Column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, unique=True)
    user_submission = Column(Text, nullable=False)
    audio_url = Column(String, nullable=True)
    overall_band = Column(Float, nullable=True)
    criteria_scores = Column(JSONB, nullable=False, default=dict)
    general_feedback = Column(Text, nullable=False, default="")
    detailed_feedback = Column(Text, nullable=False, default="")
    grammar_corrections = Column(JSONB, default=list)
    provider_used = Column(String, nullable=False, default="gemini")
    ai_model_used = Column(String, nullable=True)
    tokens_used = Column(Integer, nullable=True)
    processing_time_ms = Column(Integer, nullable=True)
    feedback_unlocks_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    upgraded_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    exam = relationship("Exam", back_populates="evaluation")
