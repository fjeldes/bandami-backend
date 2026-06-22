"""
Review request model for GDPR Art.22 "right to human intervention" on automated decisions.
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.dialects.postgresql import UUID
from app.db.engine import Base
from datetime import datetime, timezone
import uuid


def _utcnow():
    return datetime.now(timezone.utc)


class ReviewRequest(Base):
    """User appeals against AI-generated band scores."""
    __tablename__ = "review_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id = Column(UUID(as_uuid=True), ForeignKey("evaluations.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    reviewer_id = Column(UUID(as_uuid=True), nullable=True)
    reviewer_notes = Column(Text, nullable=True)
    resolved_band = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
