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


@router.post("/me/study-plan")
async def generate_study_plan(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    plan_info: dict = Depends(get_user_plan_info),
):
    """Generate a personalized 7-day study plan based on recent performance (premium only)."""
    is_premium = plan_info.get("tier", "free") == "premium" or plan_info.get("is_admin", False)
    if not is_premium:
        raise HTTPException(status_code=402, detail="Study plan is a Premium feature")

    from app.services.providers import get_provider

    recent = (
        db.query(Evaluation)
        .join(Exam)
        .filter(Exam.user_id == user_id, Evaluation.overall_band.isnot(None))
        .order_by(desc(Evaluation.created_at))
        .limit(10)
        .all()
    )

    if not recent:
        return {"plan": [], "message": "Complete at least one exam to generate a study plan."}

    # Build summary
    exam_summaries = []
    for ev in recent:
        exam = ev.exam
        criteria_summary = ", ".join(
            [f"{k.replace('_', ' ')}: {v['score']}" for k, v in (ev.criteria_scores or {}).items() if isinstance(v, dict) and 'score' in v][:4]
        )
        exam_summaries.append(
            f"{exam.exam_type.title()} — Band {ev.overall_band} — {criteria_summary}"
        )

    weak_areas = []
    if recent:
        all_criteria: dict[str, list[float]] = {}
        for ev2 in recent:
            for k, v in (ev2.criteria_scores or {}).items():
                if isinstance(v, dict) and 'score' in v:
                    all_criteria.setdefault(k, []).append(v['score'])
        for k, scores in all_criteria.items():
            avg = sum(scores) / len(scores)
            if avg < 6.5:
                weak_areas.append(f"{k.replace('_', ' ')} (avg {avg:.1f})")

    prompt = (
        "You are an IELTS study coach. Based on the student's recent performance, create a concise 7-day study plan.\n\n"
        f"Recent exams:\n" + "\n".join(exam_summaries[-5:]) + "\n\n"
        f"Weak areas: {', '.join(weak_areas) if weak_areas else 'None identified yet'}\n\n"
        "Return ONLY valid JSON. Give 5-7 specific daily tasks (each with day number, focus area, and 1-2 sentence instruction).\n"
        '{"plan": [{"day": 1, "focus": "Writing Task 2 Structure", "task": "..."}, ...], "message": "..."}'
    )

    try:
        provider = get_provider("gemini")
        ai_result = await provider.evaluate_speaking("", detailed=False)
        # Use the AI to generate the plan — simple approach: call gemini directly
        import google.genai as genai_import
        from app.core.config import get_settings
        s = get_settings()
        client = genai_import.Client(api_key=s.gemini_api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config={"temperature": 0.7, "max_output_tokens": 2048, "response_mime_type": "application/json"},
        )
        import json
        data = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
        return data
    except Exception as e:
        # Fallback: hardcoded plan from weak areas
        if not weak_areas:
            return {"plan": [
                {"day": 1, "focus": "Writing Task 2", "task": "Write a full Task 2 essay under timed conditions (40 min)."},
                {"day": 2, "focus": "Speaking Part 2", "task": "Record a 2-minute long turn on an unfamiliar topic. Review your fluency."},
                {"day": 3, "focus": "Grammar Review", "task": "Review common grammatical errors in your recent exams. Write 10 corrected sentences."},
                {"day": 4, "focus": "Vocabulary Building", "task": "Learn 15 new academic words and use each in a sentence related to common IELTS topics."},
                {"day": 5, "focus": "Writing Task 1", "task": "Describe a graph/chart in 150 words (20 min). Focus on accurate data description."},
                {"day": 6, "focus": "Speaking Part 3", "task": "Practice abstract discussion questions. Record 3-minute answers on education and technology topics."},
                {"day": 7, "focus": "Full Practice", "task": "Complete one full Writing Task 2 + Speaking Part 1 practice. Review all feedback from this week."},
            ], "message": "Plan generated from your weak areas. Keep practicing!"}
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
