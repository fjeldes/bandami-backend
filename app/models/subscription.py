import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, ForeignKey, Text, JSON, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.engine import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="usd")
    interval = Column(String, nullable=False, default="month")
    daily_eval_limit = Column(Integer, nullable=False, default=0)
    provider = Column(String, nullable=False, default="gemini")
    feedback_delay_hours = Column(Integer, nullable=False, default=0)
    stripe_price_id = Column(String, nullable=True)
    stripe_product_id = Column(String, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=False)
    status = Column(String, nullable=False, default="active")
    stripe_subscription_id = Column(String, nullable=True)
    stripe_session_id = Column(String, nullable=True, unique=True)
    current_period_start = Column(DateTime(timezone=True), default=_now, nullable=False)
    current_period_end = Column(DateTime(timezone=True), default=_now, nullable=False)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
    auto_renew = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user = relationship("UserProfile", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan")

Index("ix_user_subscriptions_stripe_session_id", UserSubscription.stripe_session_id)


class UserCreditPack(Base):
    __tablename__ = "user_credit_packs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    credits_total = Column(Integer, nullable=False)
    credits_used = Column(Integer, nullable=False, default=0)
    source = Column(String, nullable=False, default="purchase")
    stripe_session_id = Column(String, nullable=True)
    purchased_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("UserProfile", back_populates="credit_packs")


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Integer, nullable=False)
    transaction_type = Column(String, nullable=False)
    description = Column(String, nullable=True)
    stripe_payment_id = Column(String, nullable=True)
    meta = Column("metadata", JSON, nullable=True, default=dict)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    user = relationship("UserProfile", back_populates="transactions")


class UserPayment(Base):
    __tablename__ = "user_payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("user_subscriptions.id", ondelete="SET NULL"), nullable=True)
    amount_clp = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="CLP")
    flow_order = Column(String, nullable=True)
    flow_invoice_id = Column(String, nullable=True)
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)
    payment_type = Column(String, nullable=False, default="recurring")
    status = Column(String, nullable=False, default="paid")
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)

    user = relationship("UserProfile", back_populates="payments")
    subscription = relationship("UserSubscription")
