"""
Consent management models for GDPR and Chilean data protection law compliance.
"""
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from app.db.engine import Base
from datetime import datetime, timezone
import uuid


def _utcnow():
    return datetime.now(timezone.utc)


class LegalDocument(Base):
    """Versioned legal documents users consent to."""
    __tablename__ = "legal_documents"

    id = Column(String, primary_key=True)
    doc_type = Column(String, nullable=False, index=True)
    version = Column(String, nullable=False)
    content_hash = Column(String, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    active = Column(Boolean, default=True)


class UserConsent(Base):
    """Individual consent records. Required for GDPR Art.7 and Chilean Law 19.628."""
    __tablename__ = "user_consents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(String, ForeignKey("legal_documents.id"), nullable=False)
    consent_type = Column(String, nullable=False, index=True)
    granted = Column(Boolean, nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
