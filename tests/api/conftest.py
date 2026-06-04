"""Fixtures for API integration tests using real PostgreSQL."""
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sa_create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta

from app.db.engine import Base
from app.models.user import UserProfile
from app.models.subscription import SubscriptionPlan, UserSubscription
from app.core.security import hash_password, create_access_token
from app.services.providers.base import (
    AIEvaluationResult,
    WritingEvaluator,
    SpeakingEvaluator,
    ReadingEvaluator,
    ListeningEvaluator,
)
from app.main import app
from app.db import deps
from sqlalchemy import create_engine as sa_create_engine, text

TEST_DATABASE_URL = "postgresql://ielts:ielts@db:5432/ielts_test"

VIEW_SQL = """
CREATE OR REPLACE VIEW user_dashboard_stats AS
SELECT
    up.id AS user_id,
    up.subscription_tier,
    4 AS daily_eval_limit,
    (SELECT COUNT(*) FROM exams WHERE user_id = up.id) AS daily_evals_used,
    (SELECT COUNT(*) FROM exams WHERE user_id = up.id) AS total_exams,
    NULL::numeric AS average_band,
    NULL::numeric AS highest_band,
    0 AS writing_exams,
    0 AS speaking_exams,
    0 AS completed_exams,
    0 AS extra_credits_available
FROM user_profiles up
GROUP BY up.id, up.subscription_tier;
"""


class MockAIProvider(WritingEvaluator, SpeakingEvaluator, ReadingEvaluator, ListeningEvaluator):
    @property
    def provider_name(self) -> str: return "gemini"

    async def evaluate_writing(self, text: str, task_type: str, detailed: bool = True) -> AIEvaluationResult:
        return AIEvaluationResult(
            overall_band=7.0,
            criteria_scores={
                "task_response": {"score": 7.0, "comment": "Good response."},
                "coherence_and_cohesion": {"score": 7.0, "comment": "Well organized."},
                "lexical_resource": {"score": 7.5, "comment": "Nice vocabulary."},
                "grammatical_range_and_accuracy": {"score": 6.5, "comment": "Some errors."},
            },
            detailed_feedback="Good essay overall." if detailed else "",
            grammar_corrections=[{"original": "she go", "corrected": "she goes", "explanation": "SVA"}],
            model="mock-model", tokens=500, processing_time_ms=200,
        )

    async def transcribe_audio(self, audio_bytes: bytes, filename: str) -> str:
        return "This is a mock transcription."

    async def evaluate_speaking(self, transcription: str, detailed: bool = True) -> AIEvaluationResult:
        return AIEvaluationResult(
            overall_band=6.5,
            criteria_scores={
                "fluency_and_coherence": {"score": 6.5, "comment": "Flows okay."},
                "lexical_resource": {"score": 6.0, "comment": "Adequate vocab."},
                "grammatical_range_and_accuracy": {"score": 6.0, "comment": "Some errors."},
                "pronunciation": {"score": 7.0, "comment": "Clear."},
            },
            detailed_feedback="Good speaking." if detailed else "",
            grammar_corrections=[{"original": "I goes", "corrected": "I go", "explanation": "SVA"}],
            transcription=transcription, model="mock-model", tokens=300, processing_time_ms=150,
        )

    async def evaluate_reading(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        return AIEvaluationResult(overall_band=8.0, criteria_scores={}, detailed_feedback="done",
                                  grammar_corrections=[], model="mock", tokens=100, processing_time_ms=100)

    async def evaluate_listening(self, answers: dict, detailed: bool = True) -> AIEvaluationResult:
        return AIEvaluationResult(overall_band=7.5, criteria_scores={}, detailed_feedback="done",
                                  grammar_corrections=[], model="mock", tokens=100, processing_time_ms=100)


def _create_test_database():
    import psycopg2
    conn = psycopg2.connect("postgresql://ielts:ielts@db:5432/ielts")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = 'ielts_test'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE ielts_test")
    cur.close()
    conn.close()


def _init_test_db(engine):
    with engine.connect() as conn:
        conn.execute(text("DROP VIEW IF EXISTS user_dashboard_stats CASCADE"))
        conn.commit()

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        conn.execute(text(VIEW_SQL))
        conn.commit()


@pytest.fixture(scope="session")
def test_engine():
    engine = sa_create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    _init_test_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine):
    with test_engine.connect() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"TRUNCATE TABLE {table.name} CASCADE"))
        conn.commit()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = SessionLocal()

    for plan in [
        SubscriptionPlan(id=str(uuid.uuid4()), slug="free", name="Free", daily_eval_limit=4,
                         provider="gemini", feedback_delay_hours=24, sort_order=1, price_cents=0, interval="month"),
        SubscriptionPlan(id=str(uuid.uuid4()), slug="premium", name="Premium", daily_eval_limit=30,
                         provider="openai", feedback_delay_hours=0, sort_order=10, price_cents=1499, interval="month"),
    ]:
        db.add(plan)
    db.commit()

    yield db

    db.close()


@pytest.fixture(scope="function")
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[deps.get_db] = override_get_db

    from app.core.auth import get_ai_provider as _gai
    async def mock_get_ai_provider(plan: dict = None):
        return MockAIProvider()
    app.dependency_overrides[_gai] = mock_get_ai_provider

    app.state.limiter.reset()

    with TestClient(app) as tc:
        yield tc

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def auth_headers(db_session, client):
    uid = str(uuid.uuid4())
    user = UserProfile(
        id=uid, email=f"test-{uid[:8]}@bandami.com",
        hashed_password=hash_password("SecurePass123!"),
        full_name="Test User", email_confirmed_at=datetime.now(timezone.utc),
        subscription_tier="free",
    )
    db_session.add(user)
    db_session.flush()

    free_plan = db_session.query(SubscriptionPlan).filter(SubscriptionPlan.slug == "free").first()
    db_session.add(UserSubscription(
        id=str(uuid.uuid4()), user_id=user.id, plan_id=free_plan.id,
        status="active", current_period_end=datetime.now(timezone.utc) + timedelta(days=365),
    ))
    db_session.commit()

    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture(scope="function")
def admin_headers(db_session, client):
    user = UserProfile(
        id=str(uuid.uuid4()), email="admin@bandami.com",
        hashed_password=hash_password("Admin123!"), full_name="Admin User",
        email_confirmed_at=datetime.now(timezone.utc), subscription_tier="premium",
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
