"""
Payment router — delegates to the configured PaymentProvider (Stripe, Paddle, etc.).
"""

import logging
from fastapi import APIRouter, HTTPException, Request, Depends, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.db.deps import get_db
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.services.payments import get_payment_provider
from app.services.payments.base import PaymentProvider
from app.models.user import UserProfile
from app.models.subscription import UserSubscription, SubscriptionPlan

router = APIRouter()


class CheckoutRequest(BaseModel):
    plan_slug: str
    success_url: str = ""
    cancel_url: str = ""
    discount_percent: int = 0


def _get_provider() -> PaymentProvider:
    return get_payment_provider()


@router.post("/create-checkout")
async def create_checkout(
    body: CheckoutRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Block purchase if already have active subscription
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    active = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id,
        UserSubscription.status.in_(["active", "trialing"]),
        UserSubscription.current_period_end > now,
    ).first()
    if active:
        raise HTTPException(status_code=400, detail="You already have an active subscription.")

    s = get_settings()
    success = body.success_url or f"{s.frontend_url}/settings?checkout=success"
    cancel = body.cancel_url or f"{s.frontend_url}/pricing"

    try:
        result = await provider.create_checkout(
            plan_slug=body.plan_slug, user_id=user_id, user_email=user.email or "",
            success_url=success, cancel_url=cancel, discount_percent=body.discount_percent,
        )
        return result
    except Exception as e:
        logger.exception("Checkout creation failed for plan=%s", body.plan_slug)
        raise HTTPException(status_code=500, detail="Checkout creation failed. Please try again.")


@router.post("/webhook")
async def payment_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    payload = await request.body()

    if provider.provider_name == "flow":
        signature = ""
    else:
        signature = request.headers.get(
            "stripe-signature" if provider.provider_name == "stripe" else "paddle-signature", ""
        )

    try:
        event = await provider.handle_webhook(payload, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    result = await provider.process_webhook_event(
        event, db, UserProfile, UserSubscription, SubscriptionPlan,
    )
    return result


@router.post("/flow/card-callback")
async def flow_card_callback(
    request: Request,
    user_id: str = Query(...),
    plan_slug: str = Query(...),
    ctx: str = Query(""),
    db: Session = Depends(get_db),
):
    import json
    provider = _get_provider()
    if provider.provider_name != "flow":
        return HTMLResponse(content="Invalid provider", status_code=400)

    success_url = ""
    cancel_url = ""
    if ctx:
        try:
            c = json.loads(ctx)
            success_url = c.get("su", "")
            cancel_url = c.get("ca", "")
        except (json.JSONDecodeError, TypeError):
            pass

    if not success_url:
        s = get_settings()
        success_url = f"{s.frontend_url}/settings?checkout=success"
    if not cancel_url:
        s = get_settings()
        cancel_url = f"{s.frontend_url}/pricing"

    form = await request.form()
    token = form.get("token", "")

    result = await provider.handle_card_callback(token, user_id, plan_slug, db, ctx)

    if result["status"] == "ok":
        first_charge = result.get("first_charge_amount", 2.99)
        next_charge = result.get("next_charge_amount", 14.99)
        sep = "&" if "?" in success_url else "?"
        success_url = f"{success_url}{sep}first_charge={first_charge}&next_charge={next_charge}"
        return RedirectResponse(url=success_url, status_code=303)
    logger.warning("Card callback failed: %s", result.get("reason", ""))
    return RedirectResponse(url=cancel_url, status_code=303)


@router.post("/flow/card-update-callback")
async def flow_card_update_callback(
    request: Request,
    user_id: str = Query(...),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    if provider.provider_name != "flow":
        return HTMLResponse(content="Invalid provider", status_code=400)

    form = await request.form()
    token = form.get("token", "")
    if not token:
        return RedirectResponse(url=f"{get_settings().frontend_url}/settings", status_code=303)

    try:
        status = await provider._get_register_status(token)
        if status.get("status") == "1":
            logger.info("Card updated for user %s", user_id)
    except Exception:
        logger.exception("Card update verification failed")

    return RedirectResponse(url=f"{get_settings().frontend_url}/settings?checkout=success", status_code=303)


@router.get("/verify-session")
async def verify_checkout_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Client-side fallback: verify a checkout session and provision if paid."""
    provider = _get_provider()

    if provider.provider_name == "paddle":
        from app.services.payments.paddle_provider import PaddleProvider
        paddle = provider  # type: PaddleProvider
        return await paddle.verify_transaction(
            session_id, user_id, db, UserProfile, UserSubscription, SubscriptionPlan,
        )

    if provider.provider_name != "stripe":
        return {"status": "skipped", "reason": "not supported for this provider"}

    import stripe
    from datetime import datetime, timezone, timedelta
    from uuid import uuid4

    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    if session.payment_status != "paid":
        return {"status": "pending", "message": "Payment not completed yet"}

    try:
        sid_user = session["metadata"]["user_id"] if "metadata" in session and "user_id" in (session["metadata"] or {}) else None
        plan_slug = session["metadata"]["plan_slug"] if "metadata" in session and "plan_slug" in (session["metadata"] or {}) else None
    except (KeyError, TypeError):
        sid_user = None
        plan_slug = None

    if sid_user != user_id:
        raise HTTPException(status_code=403, detail="Session does not belong to this user")
    if not plan_slug:
        return {"status": "skipped"}

    if db.query(UserSubscription).filter(UserSubscription.stripe_session_id == session_id).first():
        return {"status": "already_processed"}

    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if user and not user.stripe_customer_id and session.customer:
        user.stripe_customer_id = session.customer
        db.commit()

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
    if not plan or plan_slug not in ("premium", "exam_week_pass"):
        return {"status": "skipped"}

    now = datetime.now(timezone.utc)
    days = 7

    us = UserSubscription(
        id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
        status="active", current_period_start=now, current_period_end=now + timedelta(days=days),
        stripe_session_id=session_id,
    )
    db.add(us)
    db.query(UserProfile).filter(UserProfile.id == user_id).update({"subscription_tier": "premium"})

    # For Premium (payment mode): create $14.99/month subscription with 7-day trial
    if plan_slug == "premium" and session.customer:
        try:
            pi = stripe.PaymentIntent.retrieve(session.payment_intent)
            pm = pi.payment_method

            trial_end = int((now + timedelta(days=7)).timestamp())
            stripe_sub = stripe.Subscription.create(
                customer=session.customer,
                items=[{"price": s.stripe_price_premium}],
                trial_end=trial_end,
                default_payment_method=pm,
                off_session=True,
                metadata={"user_id": user_id, "plan_slug": "premium"},
            )
            us.stripe_subscription_id = stripe_sub.id
        except Exception:
            logger.exception("Failed to create Stripe subscription for premium user=%s", user_id)

    db.commit()
    return {"status": "ok"}


@router.post("/create-portal")
async def create_customer_portal(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    try:
        return await provider.create_portal(user_id, db, UserProfile)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/subscription")
async def get_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    info = await provider.get_subscription(user_id, db, UserSubscription)
    return {
        "has_subscription": info.has_subscription,
        "is_one_time": info.is_one_time,
        "status": info.status,
        "current_period_start": info.current_period_start,
        "current_period_end": info.current_period_end,
        "cancel_at_period_end": info.cancel_at_period_end,
        "plan_name": info.plan_name,
        "plan_slug": info.plan_slug,
        "plan_amount": info.plan_amount,
        "plan_interval": info.plan_interval,
        "card_last4": info.card_last4,
        "card_brand": info.card_brand,
    }


@router.post("/cancel")
async def cancel_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    try:
        return await provider.cancel_subscription(user_id, db, UserSubscription)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/reactivate")
async def reactivate_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    try:
        return await provider.reactivate_subscription(user_id, db, UserSubscription)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/switch-plan")
async def switch_plan(
    body: dict,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    new_plan_slug = body.get("plan_slug")
    if new_plan_slug not in ("premium", "exam_week_pass"):
        raise HTTPException(status_code=400, detail="Invalid plan.")

    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    s = get_settings()

    try:
        return await provider.switch_plan(
            new_plan_slug, user_id, user.email or "",
            s.frontend_url, db, UserProfile, UserSubscription, SubscriptionPlan,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/invoices")
async def get_invoices(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    provider = _get_provider()
    invoices = await provider.get_invoices(user_id, db, UserProfile)
    return {"invoices": invoices}
