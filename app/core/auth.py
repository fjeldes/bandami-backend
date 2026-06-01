"""
Auth dependency — validates custom JWT and returns current user.
Uses SQLAlchemy ORM for DB access.
"""

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, func, text

from app.db.deps import get_db
from app.core.security import decode_access_token
from app.services.providers import get_provider
from app.services.providers.base import AbstractAIProvider
from app.models.user import UserProfile
from app.models.subscription import UserCreditPack, UserSubscription
from datetime import datetime, timezone, timedelta


async def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = authorization.split(" ")[1]

    try:
        user_id = decode_access_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.execute(select(UserProfile.id).where(UserProfile.id == user_id)).scalar()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return str(user_id)


def _calc_plan_info(db: Session, user_id: str) -> dict:
    """Calculate real-time tier: subscription → credits → free."""
    from app.core.config import get_settings
    settings = get_settings()

    is_admin = db.execute(select(UserProfile.role).where(UserProfile.id == user_id)).scalar() == "admin"
    if is_admin:
        return {
            "tier": "premium",
            "provider": "gemini",
            "daily_eval_limit": 999,
            "feedback_delay_hours": 0,
            "credits_remaining": 0,
            "is_admin": True,
        }

    now = datetime.now(timezone.utc)

    result = None

    sub = (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.current_period_end > now,
        )
        .order_by(UserSubscription.current_period_end.desc())
        .first()
    )

    if sub:
        plan = sub.plan
        result = {
            "tier": "premium",
            "provider": plan.provider if plan else "openai",
            "daily_eval_limit": plan.daily_eval_limit if plan else 30,
            "feedback_delay_hours": 0,
            "credits_remaining": 0,
            "is_admin": False,
        }
    else:
        packs = (
            db.query(UserCreditPack)
            .filter(
                UserCreditPack.user_id == user_id,
                UserCreditPack.credits_used < UserCreditPack.credits_total,
                (UserCreditPack.expires_at == None) | (UserCreditPack.expires_at > now),
            )
            .all()
        )
        total_remaining = sum(p.credits_total - p.credits_used for p in packs)

        if total_remaining > 0:
            result = {
                "tier": "credit_pack",
                "provider": "openai",
                "daily_eval_limit": 999,
                "feedback_delay_hours": 0,
                "credits_remaining": total_remaining,
                "is_admin": False,
            }
        else:
            result = {
                "tier": "free",
                "provider": "gemini",
                "daily_eval_limit": 4,
                "feedback_delay_hours": 24,
                "credits_remaining": 0,
                "is_admin": False,
            }

    if settings.environment == "development":
        result["provider"] = "gemini"

    return result


async def get_user_plan_info(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _calc_plan_info(db, user_id)


async def check_daily_limit(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    user_plan: dict = Depends(get_user_plan_info),
) -> dict:
    from app.core.config import get_settings
    settings = get_settings()

    if settings.environment == "development":
        user_plan["eval_source"] = "daily"
        user_plan["daily_used"] = 0
        return user_plan

    if user_plan.get("is_admin"):
        user_plan["eval_source"] = "daily"
        user_plan["daily_used"] = 0
        return user_plan

    tier = user_plan.get("tier", "free")

    if tier == "credit_pack":
        has_credit = db.scalar(text("SELECT consume_credit_pack(:uid)"), {"uid": user_id}) or False
        if not has_credit:
            raise HTTPException(status_code=402, detail="No credits remaining. Purchase a credit pack to continue.")
        user_plan["eval_source"] = "credit_pack"
        user_plan["daily_used"] = 0
        return user_plan

    used = db.scalar(
        text("SELECT COUNT(*) FROM exams WHERE user_id = :uid AND created_at::date = CURRENT_DATE AND eval_source = 'daily' AND status NOT IN ('pending', 'failed')"),
        {"uid": user_id},
    ) or 0
    limit = user_plan["daily_eval_limit"]

    if used >= limit:
        has_credit = db.scalar(text("SELECT consume_credit_pack(:uid)"), {"uid": user_id}) or False
        if not has_credit:
            raise HTTPException(
                status_code=402,
                detail=f"Daily limit reached ({used}/{limit}). Upgrade to Premium or purchase a credit pack.",
            )
        user_plan["eval_source"] = "credit_pack"
    else:
        user_plan["eval_source"] = "daily"

    user_plan["daily_used"] = used
    return user_plan


async def get_ai_provider(
    user_plan: dict = Depends(check_daily_limit),
) -> AbstractAIProvider:
    provider_name = user_plan["provider"]
    return get_provider(provider_name)


def compute_feedback_unlocks_at(delay_hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=delay_hours)


async def require_admin(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> str:
    role = db.execute(select(UserProfile.role).where(UserProfile.id == user_id)).scalar()
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user_id
