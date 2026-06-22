from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, text
from pydantic import BaseModel, Field
from datetime import datetime, timezone, timedelta
import glob
import os

from app.db.deps import get_db
from app.core.auth import get_current_user, get_user_plan_info
from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.user import UserProfile, RefreshToken
from app.models.exam import Exam, Evaluation
from app.models.subscription import UserCreditPack, SubscriptionPlan, UserSubscription
from app.models.study_plan import StudyPlan
from app.models.review import ReviewRequest
from app.models.consent import UserConsent
from app.schemas.evaluation import (
    DashboardStats, UserCreditPackResponse,
    SubscriptionPlanResponse, UserSubscriptionResponse,
)

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
    is_unlimited = plan_info.get("is_admin", False)
    limit = plan_info.get("daily_eval_limit", 4)
    used = data.get("daily_evals_used", 0)

    return DashboardStats(
        subscription_tier=data.get("subscription_tier", "free"),
        daily_eval_limit=-1 if is_unlimited else limit,
        daily_evals_used=0,
        daily_evals_remaining=-1 if is_unlimited else max(0, limit - used),
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
    total = db.query(Exam).filter(Exam.user_id == user_id).count()

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
            def _strip_criteria_feedback(criteria: dict) -> dict:
                return {k: {"score": v["score"]} for k, v in (criteria or {}).items() if isinstance(v, dict) and "score" in v}

            eval_data = {
                "id": str(ev.id),
                "overall_band": ev.overall_band,
                "criteria_scores": _strip_criteria_feedback(ev.criteria_scores) if not (is_visible or is_admin) else ev.criteria_scores,
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
    return {"exams": results, "total": total, "limit": limit, "offset": offset}


@router.get("/me/error-patterns")
async def get_error_patterns(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    """Aggregate grammar errors into patterns (premium only)."""
    is_premium = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)
    if not is_premium:
        return {"patterns": [], "premium_required": True}

    evals = (
        db.query(Evaluation)
        .join(Exam)
        .filter(Exam.user_id == user_id, Evaluation.grammar_corrections.isnot(None))
        .order_by(desc(Evaluation.created_at))
        .limit(50)
        .all()
    )

    patterns: dict[str, dict] = {}
    for ev in evals:
        for corr in (ev.grammar_corrections or []):
            explanation = (corr.get("explanation") or "").lower()
            key = _classify_error(explanation)
            if key not in patterns:
                patterns[key] = {"type": key, "count": 0, "examples": []}
            patterns[key]["count"] += 1
            if len(patterns[key]["examples"]) < 3:
                patterns[key]["examples"].append({
                    "original": corr.get("original", ""),
                    "corrected": corr.get("corrected", ""),
                })

    sorted_patterns = sorted(patterns.values(), key=lambda x: x["count"], reverse=True)[:6]
    return {"patterns": sorted_patterns}


def _classify_error(explanation: str) -> str:
    if any(w in explanation for w in ["subject-verb", "sva", "agreement"]): return "Subject-Verb Agreement"
    if any(w in explanation for w in ["tense", "past tense", "present tense", "future"]): return "Verb Tense"
    if any(w in explanation for w in ["article", "a/an", "definite", "indefinite"]): return "Articles"
    if any(w in explanation for w in ["preposition", "in/on/at"]): return "Prepositions"
    if any(w in explanation for w in ["plural", "singular", "countable"]): return "Singular/Plural"
    if any(w in explanation for w in ["word order", "sentence structure", "syntax"]): return "Word Order"
    if any(w in explanation for w in ["pronoun", "reference"]): return "Pronouns"
    if any(w in explanation for w in ["modal", "auxiliary", "helping verb"]): return "Modal/Auxiliary Verbs"
    if any(w in explanation for w in ["conditional", "if clause"]): return "Conditionals"
    if any(w in explanation for w in ["passive", "active", "voice"]): return "Passive/Active Voice"
    return "Other Grammar"


MAX_PLANS_PER_MONTH = 4


def _calc_weak_areas(recent: list[Evaluation]) -> list[str]:
    all_criteria: dict[str, list[float]] = {}
    for ev in recent:
        for k, v in (ev.criteria_scores or {}).items():
            if isinstance(v, dict) and "score" in v:
                all_criteria.setdefault(k, []).append(v["score"])
    weak = []
    for k, scores in all_criteria.items():
        avg = sum(scores) / len(scores)
        if avg < 6.5:
            weak.append(f"{k.replace('_', ' ')} (avg {avg:.1f})")
    return weak


@router.get("/me/study-plan")
async def get_study_plan(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    """Return the latest study plan for the current user."""
    plan = (
        db.query(StudyPlan)
        .filter(StudyPlan.user_id == user_id)
        .order_by(desc(StudyPlan.created_at))
        .first()
    )
    if not plan:
        return {"plan": None, "message": "", "can_generate": True, "remaining_this_month": MAX_PLANS_PER_MONTH}

    from datetime import timezone as dt_tz
    now = datetime.now(dt_tz.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_count = db.query(StudyPlan).filter(
        StudyPlan.user_id == user_id,
        StudyPlan.created_at >= month_start,
    ).count()
    remaining = MAX_PLANS_PER_MONTH - month_count

    plan_data = plan.plan_data if isinstance(plan.plan_data, dict) else {}
    return {
        "id": str(plan.id),
        "plan": plan_data.get("plan", []),
        "message": plan_data.get("message", plan.message or ""),
        "can_generate": remaining > 0,
        "remaining_this_month": remaining,
        "month_limit": MAX_PLANS_PER_MONTH,
    }


@router.post("/me/study-plan")
async def generate_study_plan(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    """Generate a personalized 7-day study plan (premium only, max 4/month)."""
    is_premium = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)
    if not is_premium:
        raise HTTPException(status_code=402, detail="Study plan is a Pro feature")

    from datetime import timezone as dt_tz
    now = datetime.now(dt_tz.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_count = db.query(StudyPlan).filter(
        StudyPlan.user_id == user_id,
        StudyPlan.created_at >= month_start,
    ).count()

    if month_count >= MAX_PLANS_PER_MONTH:
        raise HTTPException(
            status_code=429,
            detail=f"You've used all {MAX_PLANS_PER_MONTH} plans this month. New plans unlock next month.",
        )

    recent = (
        db.query(Evaluation)
        .join(Exam)
        .filter(Exam.user_id == user_id, Evaluation.overall_band.isnot(None))
        .order_by(desc(Evaluation.created_at))
        .limit(10)
        .all()
    )

    if not recent:
        return {"plan": [], "message": "Complete at least one exam to generate a study plan.", "can_generate": True, "remaining_this_month": MAX_PLANS_PER_MONTH - month_count}

    # Build exam summaries
    exam_summaries = []
    for ev in recent[:5]:
        exam = ev.exam
        criteria_summary = ", ".join(
            [f"{k.replace('_', ' ')}: {v['score']}" for k, v in (ev.criteria_scores or {}).items() if isinstance(v, dict) and "score" in v][:4]
        )
        exam_summaries.append(
            f"{exam.exam_type.title()} — Band {ev.overall_band} — {criteria_summary}"
        )

    weak_areas = _calc_weak_areas(recent)

    prompt = (
        "You are an IELTS study coach. Based on the student's recent performance, create a concise 7-day study plan.\n\n"
        f"Recent exams:\n" + "\n".join(exam_summaries) + "\n\n"
        f"Weak areas: {', '.join(weak_areas) if weak_areas else 'None identified yet'}\n\n"
        "Return ONLY valid JSON. Give 5-7 specific daily tasks (each with day number, focus area, and 1-2 sentence instruction).\n"
        '{"plan": [{"day": 1, "focus": "Writing Task 2 Structure", "task": "..."}, ...], "message": "..."}'
    )

    try:
        from app.services.plan_generator import generate_plan as gen_plan
        data = await gen_plan(prompt)
    except Exception as e:
        logger = __import__("logging").getLogger("ielts.users")
        logger.warning(f"AI plan generation failed, using fallback: {e}")
        data = _fallback_plan(weak_areas)

    # Add completion tracking to each day
    plan_items = data.get("plan", [])
    for item in plan_items:
        item["completed"] = False

    plan_record = StudyPlan(
        user_id=user_id,
        plan_data={"plan": plan_items, "message": data.get("message", "")},
        weak_areas=weak_areas,
        message=data.get("message", ""),
    )
    db.add(plan_record)
    db.commit()
    db.refresh(plan_record)

    remaining = MAX_PLANS_PER_MONTH - (month_count + 1)
    return {
        "id": str(plan_record.id),
        "plan": plan_items,
        "message": data.get("message", ""),
        "can_generate": remaining > 0,
        "remaining_this_month": remaining,
        "month_limit": MAX_PLANS_PER_MONTH,
    }


@router.patch("/me/study-plan/{plan_id}")
async def update_study_plan_day(
    plan_id: str,
    body: dict,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Toggle a day's completed status in a study plan."""
    plan = db.query(StudyPlan).filter(StudyPlan.id == plan_id, StudyPlan.user_id == user_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Study plan not found")

    day_number = body.get("day")
    completed = body.get("completed", True)

    plan_data = plan.plan_data if isinstance(plan.plan_data, dict) else {}
    plan_items = plan_data.get("plan", [])
    updated = False
    for item in plan_items:
        if item.get("day") == day_number:
            item["completed"] = completed
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Day not found in plan")

    plan.plan_data = {"plan": plan_items, "message": plan_data.get("message", "")}
    db.commit()
    return {"day": day_number, "completed": completed}


def _fallback_plan(weak_areas: list[str]) -> dict:
    if not weak_areas:
        return {"plan": [
            {"day": 1, "focus": "Writing Task 2", "task": "Write a full Task 2 essay under timed conditions (40 min)."},
            {"day": 2, "focus": "Speaking Part 2", "task": "Record a 2-minute long turn on an unfamiliar topic. Review your fluency."},
            {"day": 3, "focus": "Grammar Review", "task": "Review common grammatical errors in your recent exams. Write 10 corrected sentences."},
            {"day": 4, "focus": "Vocabulary Building", "task": "Learn 15 new academic words and use each in a sentence related to common IELTS topics."},
            {"day": 5, "focus": "Writing Task 1", "task": "Describe a graph/chart in 150 words (20 min). Focus on accurate data description."},
            {"day": 6, "focus": "Speaking Part 3", "task": "Practice abstract discussion questions. Record 3-minute answers on education and technology topics."},
            {"day": 7, "focus": "Full Practice", "task": "Complete one full Writing Task 2 + Speaking Part 1 practice. Review all feedback from this week."},
        ], "message": "Keep practicing! Focus on consistency."}
    focus = weak_areas[0].split(" ")[0] if weak_areas else "Grammar"
    return {"plan": [
        {"day": 1, "focus": f"Improve {focus}", "task": f"Practice {focus.lower()} with targeted exercises. Focus on accuracy first, then speed."},
        {"day": 2, "focus": "Vocabulary", "task": "Learn 10 new words related to common IELTS topics. Use them in speaking practice."},
        {"day": 3, "focus": "Writing Task 2", "task": "Write a full essay in 40 minutes. Self-correct grammar errors before submitting."},
        {"day": 4, "focus": "Speaking Part 2", "task": "Record yourself speaking for 2 minutes on a random topic. Listen back and note improvements."},
        {"day": 5, "focus": "Grammar Review", "task": "Review your most common errors. Write 10 sentences practicing the correct forms."},
        {"day": 6, "focus": "Full Practice", "task": "Do one Writing + one Speaking evaluation. Compare scores to your target."},
        {"day": 7, "focus": "Rest & Review", "task": "Review all feedback from this week. Note your biggest improvement and set goals for next week."},
    ], "message": f"Focus on improving your {focus.lower()} this week."}


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
            UserSubscription.status.in_(["active", "trialing"]),
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
    module: str = None,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    from app.models.exam import Question
    query = db.query(Question).filter(Question.is_active == True)

    # Free users: only access Speaking Part 1
    if plan_info.get("tier") == "free" and not plan_info.get("is_admin"):
        if exam_type == "speaking":
            query = query.filter(Question.module == "part1")
        elif exam_type is None:
            # If no exam_type filter, restrict speaking questions
            query = query.filter(
                (Question.exam_type != "speaking") |
                ((Question.exam_type == "speaking") & (Question.module == "part1"))
            )

    if exam_type:
        query = query.filter(Question.exam_type == exam_type)
    if task_type:
        query = query.filter(Question.task_type == task_type)
    if difficulty:
        query = query.filter(Question.difficulty == difficulty)
    if module:
        query = query.filter(Question.module == module)

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
    return {
        "referral_code": user.referral_code,
        "referral_count": referrals,
        "referral_url": f"/register?ref={user.referral_code}",
        "referral_discounts": user.referral_discounts or 0,
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
    referrer.referral_discounts = (referrer.referral_discounts or 0) + 1
    db.commit()
    return {"status": "ok", "message": "Referral applied! 50% off on your next Week Pass."}


# ---- Privacy & Data Rights (GDPR / Chilean Law 19.628) ----

class AppealRequest(BaseModel):
    reason: str = ""


@router.delete("/me")
async def delete_account(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """GDPR Art.17 Right to Erasure. Hard-deletes content, pseudo-anonymizes profile for financial integrity."""
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    audio_dir = "/app/static/audio"
    if os.path.exists(audio_dir):
        for f in glob.glob(os.path.join(audio_dir, f"*{user_id}*")):
            try:
                os.remove(f)
            except OSError:
                pass

    exam_ids = [e[0] for e in db.query(Exam.id).filter(Exam.user_id == user_id).all()]
    if exam_ids:
        db.query(Evaluation).filter(Evaluation.exam_id.in_(exam_ids)).delete(synchronize_session=False)
        db.query(Exam).filter(Exam.user_id == user_id).delete(synchronize_session=False)

    db.query(StudyPlan).filter(StudyPlan.user_id == user_id).delete(synchronize_session=False)
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete(synchronize_session=False)
    db.query(UserConsent).filter(UserConsent.user_id == user_id).delete(synchronize_session=False)

    user.email = f"deleted-{user.id}@anonymous.bandami.com"
    user.full_name = None
    user.hashed_password = None
    user.google_id = None
    user.avatar_url = None
    user.referred_by = None
    user.subscription_tier = "deleted"

    db.commit()
    return {"status": "deleted"}


@router.post("/me/evaluations/{exam_id}/appeal")
async def appeal_evaluation(
    exam_id: str,
    body: AppealRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """GDPR Art.22 — Right to human intervention on automated decisions."""
    evaluation = db.query(Evaluation).filter(Evaluation.exam_id == exam_id).first()
    if not evaluation:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    existing = db.query(ReviewRequest).filter(
        ReviewRequest.evaluation_id == evaluation.id,
        ReviewRequest.user_id == user_id,
    ).first()
    if existing:
        return {"status": existing.status, "review_id": str(existing.id)}

    review = ReviewRequest(
        evaluation_id=str(evaluation.id),
        user_id=user_id,
        reason=body.reason,
    )
    db.add(review)
    db.commit()
    return {"status": "pending", "review_id": str(review.id)}


@router.get("/me/reviews")
async def list_my_reviews(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reviews = db.query(ReviewRequest).filter(
        ReviewRequest.user_id == user_id,
    ).order_by(ReviewRequest.created_at.desc()).all()
    return [{
        "id": str(r.id),
        "evaluation_id": str(r.evaluation_id),
        "status": r.status,
        "reason": r.reason,
        "resolved_band": r.resolved_band,
        "reviewer_notes": r.reviewer_notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    } for r in reviews]


# ---- Consent Management (GDPR Art.7) ----

class ConsentRequest(BaseModel):
    consent_type: str
    document_id: str
    granted: bool = True


@router.post("/me/consent")
async def record_consent(
    body: ConsentRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record a user consent decision for audit trail."""
    consent = UserConsent(
        user_id=user_id,
        document_id=body.document_id,
        consent_type=body.consent_type,
        granted=body.granted,
    )
    db.add(consent)
    db.commit()
    return {"status": "recorded"}
