from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, text
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta

from app.db.deps import get_db
from app.core.auth import get_current_user, get_user_plan_info
from app.core.security import hash_password, verify_password
from app.models.user import UserProfile
from app.models.exam import Exam, Evaluation
from app.models.subscription import UserCreditPack, SubscriptionPlan, UserSubscription
from app.schemas.evaluation import (
    DashboardStats, UserCreditPackResponse,
    SubscriptionPlanResponse, UserSubscriptionResponse,
)
from datetime import datetime, timezone

router = APIRouter()


@router.get("/me/stats", response_model=DashboardStats)
async def get_user_stats(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    row = db.execute(
        text("SELECT * FROM user_dashboard_stats WHERE user_id = :uid"),
        {"uid": user_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User stats not found")

    data = dict(row._mapping)
    limit = plan_info.get("daily_eval_limit", 4)
    used = data.get("daily_evals_used", 0)

    return DashboardStats(
        subscription_tier=data.get("subscription_tier", "free"),
        daily_eval_limit=limit,
        daily_evals_used=used,
        daily_evals_remaining=max(0, limit - used),
        total_exams=data.get("total_exams", 0),
        average_band=data.get("average_band"),
        highest_band=data.get("highest_band"),
        writing_exams=data.get("writing_exams", 0),
        speaking_exams=data.get("speaking_exams", 0),
        completed_exams=data.get("completed_exams", 0),
        extra_credits_available=data.get("extra_credits_available", 0),
    )


@router.get("/me/exams")
async def get_user_exams(
    limit: int = 20,
    offset: int = 0,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    exams = (
        db.query(Exam)
        .outerjoin(Evaluation)
        .filter(Exam.user_id == user_id)
        .order_by(desc(Exam.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for exam in exams:
        ev = exam.evaluation

        is_visible = True
        if ev and ev.feedback_unlocks_at:
            unlocks_at = ev.feedback_unlocks_at
            if unlocks_at.tzinfo is None:
                unlocks_at = unlocks_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            is_visible = unlocks_at <= now

        is_admin = plan_info.get("is_admin", False)

        exam_data = {
            "id": str(exam.id),
            "user_id": str(exam.user_id),
            "question_id": str(exam.question_id) if exam.question_id else None,
            "exam_type": exam.exam_type,
            "task_type": exam.task_type,
            "status": exam.status,
            "attempt_number": exam.attempt_number,
            "time_taken_seconds": exam.time_taken_seconds,
            "eval_source": exam.eval_source,
            "created_at": exam.created_at.isoformat() if exam.created_at else None,
            "completed_at": exam.completed_at.isoformat() if exam.completed_at else None,
            "evaluations": [],
        }

        if ev:
            eval_data = {
                "id": str(ev.id),
                "overall_band": ev.overall_band,
                "criteria_scores": ev.criteria_scores if (is_visible or is_admin) else {},
                "general_feedback": ev.general_feedback or "",
                "detailed_feedback": ev.detailed_feedback if (is_visible or is_admin) else None,
                "grammar_corrections": ev.grammar_corrections if (is_visible or is_admin) else [],
                "is_feedback_visible": is_visible or is_admin,
                "provider_used": ev.provider_used,
                "ai_model_used": ev.ai_model_used,
                "tokens_used": ev.tokens_used,
                "processing_time_ms": ev.processing_time_ms,
                "feedback_unlocks_at": ev.feedback_unlocks_at.isoformat() if ev.feedback_unlocks_at else None,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            }
            exam_data["evaluations"].append(eval_data)

        results.append(exam_data)
    return results


@router.get("/me/credit-packs", response_model=list[UserCreditPackResponse])
async def get_user_credit_packs(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = text("NOW()")
    packs = (
        db.query(UserCreditPack)
        .filter(
            UserCreditPack.user_id == user_id,
            UserCreditPack.credits_used < UserCreditPack.credits_total,
            (UserCreditPack.expires_at == None) | (UserCreditPack.expires_at > now),
        )
        .order_by(UserCreditPack.purchased_at)
        .all()
    )

    return [
        UserCreditPackResponse(
            id=str(p.id),
            credits_total=p.credits_total,
            credits_used=p.credits_used,
            credits_remaining=p.credits_total - p.credits_used,
            purchased_at=p.purchased_at,
            expires_at=p.expires_at,
        )
        for p in packs
    ]


@router.get("/plans", response_model=list[SubscriptionPlanResponse])
async def get_plans(db: Session = Depends(get_db)):
    plans = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active == True)
        .order_by(SubscriptionPlan.sort_order)
        .all()
    )
    return [{
        "id": str(p.id),
        "slug": p.slug,
        "name": p.name,
        "description": p.description,
        "price_cents": p.price_cents,
        "currency": p.currency,
        "interval": p.interval,
        "daily_eval_limit": p.daily_eval_limit,
        "provider": p.provider,
        "feedback_delay_hours": p.feedback_delay_hours,
        "sort_order": p.sort_order,
        "is_active": p.is_active,
    } for p in plans]


@router.get("/me/subscription", response_model=UserSubscriptionResponse)
async def get_my_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.current_period_end > text("NOW()"),
        )
        .order_by(desc(UserSubscription.created_at))
        .first()
    )

    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")

    return UserSubscriptionResponse(
        id=str(sub.id),
        user_id=str(sub.user_id),
        plan_id=str(sub.plan_id),
        status=sub.status,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        auto_renew=sub.auto_renew,
    )


@router.get("/questions")
async def get_questions(
    exam_type: str = None,
    task_type: str = None,
    difficulty: int = None,
    db: Session = Depends(get_db),
):
    from app.models.exam import Question
    query = db.query(Question).filter(Question.is_active == True)

    if exam_type:
        query = query.filter(Question.exam_type == exam_type)
    if task_type:
        query = query.filter(Question.task_type == task_type)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)

    questions = query.order_by(Question.difficulty).all()
    return [{
        "id": str(q.id),
        "exam_type": q.exam_type,
        "task_type": q.task_type,
        "difficulty": q.difficulty,
        "prompt_text": q.prompt_text,
        "title": q.title,
        "module": q.module,
        "is_active": q.is_active,
    } for q in questions]


class UpdateProfileRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)


@router.patch("/me/profile")
async def update_profile(
    body: UpdateProfileRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.full_name = body.full_name
    db.commit()
    return {"status": "ok", "full_name": body.full_name}


@router.post("/me/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    db.commit()
    return {"status": "ok"}


@router.get("/me/referral")
async def get_referral(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.referral_code:
        import secrets
        user.referral_code = secrets.token_hex(4).upper()[:8]
        db.commit()

    referrals = db.query(UserProfile).filter(UserProfile.referred_by == user_id).count()
    packs = db.query(UserCreditPack).filter(UserCreditPack.user_id == user_id, UserCreditPack.source == "referral").all()
    earned = sum(p.credits_total for p in packs)
    used = sum(p.credits_used for p in packs)
    return {
        "referral_code": user.referral_code,
        "referral_count": referrals,
        "referral_url": f"/register?ref={user.referral_code}",
        "credits_earned": earned,
        "credits_used": used,
        "credits_remaining": earned - used,
    }


class ApplyReferralRequest(BaseModel):
    referral_code: str


@router.post("/me/referral/apply")
async def apply_referral(
    body: ApplyReferralRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.referred_by:
        raise HTTPException(status_code=400, detail="Referral already applied")

    referrer = db.query(UserProfile).filter(UserProfile.referral_code == body.referral_code.upper()).first()
    if not referrer or str(referrer.id) == user_id:
        raise HTTPException(status_code=400, detail="Invalid referral code")

    user.referred_by = str(referrer.id)
    for uid in [user_id, str(referrer.id)]:
        db.add(UserCreditPack(user_id=uid, credits_total=2, credits_used=0, source="referral", purchased_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(days=365)))
    db.commit()
    return {"status": "ok", "message": "Referral applied! +2 credits earned."}
