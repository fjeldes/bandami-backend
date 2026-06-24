"""
Admin router: stats, users, questions, plans management.
Uses SQLAlchemy ORM.
"""

from datetime import datetime, timezone, timedelta
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc, or_, text

from app.db.deps import get_db
from app.core.auth import require_admin
from app.models.user import UserProfile
from app.models.exam import Exam, Evaluation, Question
from app.models.subscription import SubscriptionPlan, UserSubscription, UserCreditPack, UserPayment
from app.models.review import ReviewRequest
router = APIRouter(dependencies=[Depends(require_admin)])


class QuestionCreate(BaseModel):
    exam_type: str
    task_type: Optional[str] = None
    difficulty: int
    prompt_text: str
    title: Optional[str] = None
    module: Optional[str] = None


class QuestionUpdate(BaseModel):
    exam_type: Optional[str] = None
    task_type: Optional[str] = None
    difficulty: Optional[int] = None
    prompt_text: Optional[str] = None
    title: Optional[str] = None
    module: Optional[str] = None
    is_active: Optional[bool] = None


class UserUpdate(BaseModel):
    role: Optional[str] = None
    subscription_tier: Optional[str] = None


class PlanUpdate(BaseModel):
    daily_eval_limit: Optional[int] = None
    provider: Optional[str] = None
    feedback_delay_hours: Optional[int] = None
    is_active: Optional[bool] = None


# ---- Stats ----

@router.get("/stats")
async def admin_stats(db: Session = Depends(get_db)):
    users = db.query(UserProfile).all()
    exams = db.query(Exam).all()
    evals = db.query(Evaluation).all()
    subs = db.query(UserSubscription).filter(UserSubscription.status == "active").count()
    active_q = db.query(Question).filter(Question.is_active == True).count()

    total_users = len(users)
    admin_count = sum(1 for u in users if u.role == "admin")
    premium_count = sum(1 for u in users if u.subscription_tier == "premium")
    total_exams = len(exams)
    completed_exams = sum(1 for e in exams if e.status == "completed")
    scores = [e.overall_band for e in evals if e.overall_band is not None]
    avg_band = sum(scores) / max(len(scores), 1)

    now = datetime.now(timezone.utc)
    users_this_month = sum(1 for u in users if u.created_at and u.created_at.month == now.month)

    return {
        "total_users": total_users,
        "admin_count": admin_count,
        "premium_count": premium_count,
        "active_subscriptions": subs,
        "total_exams": total_exams,
        "completed_exams": completed_exams,
        "average_band": round(avg_band, 2),
        "active_questions": active_q,
        "new_users_this_month": users_this_month,
    }


# ---- Users ----

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    db: Session = Depends(get_db),
):
    query = db.query(UserProfile)
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            UserProfile.email.ilike(pattern),
            UserProfile.full_name.ilike(pattern),
        ))

    total = query.count()
    offset = (page - 1) * limit
    users = query.order_by(desc(UserProfile.created_at)).offset(offset).limit(limit).all()

    return {
        "users": [_serialize_user(u) for u in users],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    exams = db.query(Exam).filter(Exam.user_id == user_id).order_by(desc(Exam.created_at)).limit(20).all()
    subs = db.query(UserSubscription).filter(UserSubscription.user_id == user_id).order_by(desc(UserSubscription.created_at)).limit(5).all()
    packs = db.query(UserCreditPack).filter(UserCreditPack.user_id == user_id).all()

    return {
        "user": _serialize_user(user),
        "exams": [_serialize_exam(e) for e in exams],
        "subscriptions": [_serialize_sub(s) for s in subs],
        "credit_packs": [_serialize_pack(p) for p in packs],
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.role is not None:
        user.role = body.role
    if body.subscription_tier is not None:
        user.subscription_tier = body.subscription_tier

    db.commit()
    return {"status": "ok"}


# ---- Questions ----

@router.get("/questions")
async def list_questions(db: Session = Depends(get_db)):
    return [_serialize_q(q) for q in db.query(Question).order_by(Question.difficulty).all()]


@router.post("/questions")
async def create_question(
    body: QuestionCreate,
    db: Session = Depends(get_db),
):
    q = Question(
        exam_type=body.exam_type,
        task_type=body.task_type,
        difficulty=body.difficulty,
        prompt_text=body.prompt_text,
        title=body.title,
        module=body.module or "general",
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return _serialize_q(q)


@router.patch("/questions/{question_id}")
async def update_question(
    question_id: str,
    body: QuestionUpdate,
    db: Session = Depends(get_db),
):
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        if v is not None:
            setattr(q, k, v)

    db.commit()
    return {"status": "ok"}


@router.delete("/questions/{question_id}")
async def delete_question(
    question_id: str,
    db: Session = Depends(get_db),
):
    db.query(Question).filter(Question.id == question_id).delete()
    db.commit()
    return {"status": "ok"}


# ---- Plans ----

@router.get("/plans")
async def list_plans(db: Session = Depends(get_db)):
    return [_serialize_plan(p) for p in db.query(SubscriptionPlan).order_by(SubscriptionPlan.sort_order).all()]


@router.patch("/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    body: PlanUpdate,
    db: Session = Depends(get_db),
):
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        if v is not None:
            setattr(plan, k, v)

    db.commit()
    return {"status": "ok"}


# ---- Exams ----

@router.get("/exams")
async def list_exams(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total = db.query(Exam).count()
    offset = (page - 1) * limit
    exams = db.query(Exam).order_by(desc(Exam.created_at)).offset(offset).limit(limit).all()

    return {
        "exams": [_serialize_exam_with_user(e) for e in exams],
        "total": total,
        "page": page,
        "limit": limit,
    }


# ---- Serializers ----

def _serialize_user(u: UserProfile) -> dict:
    return {
        "id": str(u.id), "email": u.email, "full_name": u.full_name,
        "subscription_tier": u.subscription_tier, "role": u.role,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _serialize_exam(e: Exam) -> dict:
    ev = e.evaluation
    return {
        "id": str(e.id), "user_id": str(e.user_id), "exam_type": e.exam_type,
        "task_type": e.task_type, "status": e.status, "attempt_number": e.attempt_number,
        "eval_source": e.eval_source,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "completed_at": e.completed_at.isoformat() if e.completed_at else None,
        "evaluations": {
            "id": str(ev.id), "overall_band": ev.overall_band,
            "criteria_scores": ev.criteria_scores, "detailed_feedback": ev.detailed_feedback,
            "grammar_corrections": ev.grammar_corrections, "provider_used": ev.provider_used,
            "ai_model_used": ev.ai_model_used, "tokens_used": ev.tokens_used,
        } if ev else None,
    }


def _serialize_exam_with_user(e: Exam) -> dict:
    result = _serialize_exam(e)
    if e.user:
        result["user_profiles"] = {"full_name": e.user.full_name, "email": e.user.email}
    return result


def _serialize_sub(s: UserSubscription) -> dict:
    return {
        "id": str(s.id), "user_id": str(s.user_id), "plan_id": str(s.plan_id),
        "status": s.status,
        "current_period_start": s.current_period_start.isoformat() if s.current_period_start else None,
        "current_period_end": s.current_period_end.isoformat() if s.current_period_end else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _serialize_pack(p: UserCreditPack) -> dict:
    return {
        "id": str(p.id), "user_id": str(p.user_id),
        "credits_total": p.credits_total, "credits_used": p.credits_used,
        "purchased_at": p.purchased_at.isoformat() if p.purchased_at else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
    }


def _serialize_q(q: Question) -> dict:
    return {
        "id": str(q.id), "exam_type": q.exam_type, "task_type": q.task_type,
        "difficulty": q.difficulty, "prompt_text": q.prompt_text,
        "title": q.title, "module": q.module, "is_active": q.is_active,
    }


def _serialize_plan(p: SubscriptionPlan) -> dict:
    return {
        "id": str(p.id), "slug": p.slug, "name": p.name, "description": p.description,
        "price_cents": p.price_cents, "currency": p.currency, "interval": p.interval,
        "daily_eval_limit": p.daily_eval_limit, "provider": p.provider,
        "feedback_delay_hours": p.feedback_delay_hours, "sort_order": p.sort_order,
        "is_active": p.is_active,
    }


# ---- Settings ----

class AppConfigUpdate(BaseModel):
    updates: dict[str, str]


@router.get("/settings")
async def get_settings(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT key, value, description FROM app_config ORDER BY key")).fetchall()
    return [{ "key": r[0], "value": r[1], "description": r[2] } for r in rows]


@router.patch("/settings")
async def update_settings(body: AppConfigUpdate, db: Session = Depends(get_db)):
    for key, value in body.updates.items():
        db.execute(text("UPDATE app_config SET value = :val, updated_at = NOW() WHERE key = :key"), {"val": value, "key": key})
    db.commit()
    return {"status": "ok"}


# ---- Analytics (DAU, conversions, tier breakdown) ----

@router.get("/analytics")
async def admin_analytics(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_users = db.scalar(text("SELECT COUNT(*) FROM user_profiles")) or 0
    premium = db.scalar(text("SELECT COUNT(*) FROM user_profiles WHERE subscription_tier = 'premium'")) or 0
    free = total_users - premium

    active_this_month = db.scalar(text(
        "SELECT COUNT(*) FROM user_profiles WHERE last_active_at >= :month_start"
    ), {"month_start": month_start}) or 0
    active_premium = db.scalar(text(
        "SELECT COUNT(*) FROM user_profiles WHERE last_active_at >= :month_start AND subscription_tier = 'premium'"
    ), {"month_start": month_start}) or 0
    active_free = active_this_month - active_premium

    conversions_total = db.scalar(text("SELECT COUNT(*) FROM user_profiles WHERE upgraded_at IS NOT NULL")) or 0
    conversions_this_month = db.scalar(text(
        "SELECT COUNT(*) FROM user_profiles WHERE upgraded_at >= :month_start"
    ), {"month_start": month_start}) or 0

    dau = db.execute(text("""
        SELECT d::date AS day,
               COUNT(DISTINCT up.id) FILTER (WHERE up.subscription_tier = 'free') AS free_users,
               COUNT(DISTINCT up.id) FILTER (WHERE up.subscription_tier = 'premium') AS premium_users
        FROM generate_series(
            CAST(:start_date AS DATE),
            CURRENT_DATE,
            '1 day'::interval
        ) d
        LEFT JOIN user_profiles up ON up.last_active_at::date = d::date
        WHERE d >= CURRENT_DATE - INTERVAL '29 days'
        GROUP BY d::date
        ORDER BY d::date
    """), {"start_date": now - timedelta(days=29)}).fetchall()

    dau_data = [{"day": str(r[0]), "free": r[1], "premium": r[2]} for r in dau]

    evals_daily = db.execute(text("""
        SELECT d::date AS day,
               COUNT(e.id) FILTER (WHERE up.subscription_tier = 'free') AS free_evals,
               COUNT(e.id) FILTER (WHERE up.subscription_tier = 'premium') AS premium_evals
        FROM generate_series(
            CAST(:start_date AS DATE),
            CURRENT_DATE,
            '1 day'::interval
        ) d
        LEFT JOIN exams e ON e.created_at::date = d::date AND e.status = 'completed'
        LEFT JOIN user_profiles up ON up.id = e.user_id
        WHERE d >= CURRENT_DATE - INTERVAL '29 days'
        GROUP BY d::date
        ORDER BY d::date
    """), {"start_date": now - timedelta(days=29)}).fetchall()

    evals_data = [{"day": str(r[0]), "free": r[1], "premium": r[2]} for r in evals_daily]

    monthly_evals_free = db.scalar(text(
        "SELECT COUNT(*) FROM exams e JOIN user_profiles up ON up.id = e.user_id WHERE e.created_at >= :month_start AND e.status = 'completed' AND up.subscription_tier = 'free'"
    ), {"month_start": month_start}) or 0
    monthly_evals_premium = db.scalar(text(
        "SELECT COUNT(*) FROM exams e JOIN user_profiles up ON up.id = e.user_id WHERE e.created_at >= :month_start AND e.status = 'completed' AND up.subscription_tier = 'premium'"
    ), {"month_start": month_start}) or 0

    return {
        "summary": {
            "total_users": total_users,
            "free_users": free,
            "premium_users": premium,
            "active_this_month": active_this_month,
            "active_free": active_free,
            "active_premium": active_premium,
            "conversions_total": conversions_total,
            "conversions_this_month": conversions_this_month,
            "monthly_evals_free": monthly_evals_free,
            "monthly_evals_premium": monthly_evals_premium,
        },
        "dau": dau_data,
        "evaluations_per_day": evals_data,
    }


# ---- Review Management (GDPR Art.22 human intervention) ----

class ResolveReviewRequest(BaseModel):
    resolved_band: float
    reviewer_notes: str = ""


@router.get("/reviews")
async def list_reviews(
    status: str = "pending",
    _: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reviews = db.query(ReviewRequest).filter(
        ReviewRequest.status == status,
    ).order_by(ReviewRequest.created_at.asc()).limit(50).all()
    return [{
        "id": str(r.id),
        "evaluation_id": str(r.evaluation_id),
        "user_id": r.user_id,
        "status": r.status,
        "reason": r.reason,
        "resolved_band": r.resolved_band,
        "reviewer_notes": r.reviewer_notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    } for r in reviews]


@router.post("/reviews/{review_id}/resolve")
async def resolve_review(
    review_id: str,
    body: ResolveReviewRequest,
    admin_user: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    review = db.query(ReviewRequest).filter(ReviewRequest.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.status != "pending":
        raise HTTPException(status_code=400, detail="Review already resolved")

    review.status = "resolved"
    review.reviewer_id = admin_user
    review.reviewer_notes = body.reviewer_notes
    review.resolved_band = body.resolved_band
    review.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "resolved"}
