import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, ForeignKey, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.engine import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expires_default() -> datetime:
    return _now() + __import__("datetime").timedelta(days=7)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    full_name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=True)
    email_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    google_id = Column(String, nullable=True)
    subscription_tier = Column(String, nullable=False, default="free")
    stripe_customer_id = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    role = Column(String, nullable=False, default="user")
    referral_code = Column(String, unique=True, nullable=True)
    referred_by = Column(String, nullable=True)
    referral_discounts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    exams = relationship("Exam", back_populates="user", lazy="dynamic")
    refresh_tokens = relationship("RefreshToken", back_populates="user", lazy="dynamic")
    credit_packs = relationship("UserCreditPack", back_populates="user", lazy="dynamic")
    transactions = relationship("CreditTransaction", back_populates="user", lazy="dynamic")
    subscriptions = relationship("UserSubscription", back_populates="user", lazy="dynamic")
    payments = relationship("UserPayment", back_populates="user", lazy="dynamic")
    study_plans = relationship("StudyPlan", back_populates="user", lazy="dynamic")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), default=_expires_default, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    user = relationship("UserProfile", back_populates="refresh_tokens")
