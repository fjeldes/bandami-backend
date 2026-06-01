"""
Payment router: Stripe checkout + webhook + Customer Portal.
Uses SQLAlchemy ORM.
"""

import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.services.payment_service import create_checkout_session, handle_webhook
from app.services.email_service import send_purchase_confirmation
from app.models.user import UserProfile
from app.models.subscription import SubscriptionPlan, UserSubscription, UserCreditPack

router = APIRouter()


class CheckoutRequest(BaseModel):
    plan_slug: str
    success_url: str = ""
    cancel_url: str = ""


def _idempotent_check(db: Session, session_id: str) -> bool:
    """Return True if this session was already processed."""
    existing = db.query(UserSubscription).filter(
        UserSubscription.stripe_session_id == session_id
    ).first()
    if existing:
        return True
    existing_pack = db.query(UserCreditPack).filter(
        UserCreditPack.stripe_session_id == session_id
    ).first()
    return existing_pack is not None


def _provision_plan(db: Session, user_id: str, plan_slug: str, session_id: str, subscription_id: str | None):
    """Provision subscription or credit pack. Idempotent via session_id."""
    if _idempotent_check(db, session_id):
        return {"status": "already_processed"}

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
    if not plan:
        return {"status": "skipped", "reason": "plan not found"}

    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    now = datetime.now(timezone.utc)

    result = {"status": "ok"}

    if plan_slug in ("premium", "exam_week_pass"):
        days = 30 if plan_slug == "premium" else 7
        db.add(UserSubscription(
            id=str(uuid4()),
            user_id=user_id,
            plan_id=str(plan.id),
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=days),
            stripe_subscription_id=subscription_id,
            stripe_session_id=session_id,
        ))
        db.query(UserProfile).filter(UserProfile.id == user_id).update({"subscription_tier": "premium"})
        db.commit()
        result = {"status": "ok", "tier": "premium"}

        # Send confirmation email
        if user and user.email:
            try:
                amount = "$14.99/mo" if plan_slug == "premium" else "$4.99/7 days"
                period = "Monthly renewal" if plan_slug == "premium" else "7-day access"
                send_purchase_confirmation(
                    user.email,
                    user.full_name or "Student",
                    plan.name,
                    amount,
                    period,
                )
            except Exception:
                pass  # don't fail provisioning if email fails

    elif plan_slug in ("credit_pack_10", "credit_pack_25"):
        credits = 10 if plan_slug == "credit_pack_10" else 25
        db.add(UserCreditPack(
            id=str(uuid4()),
            user_id=user_id,
            credits_total=credits,
            credits_used=0,
            source="purchase",
            stripe_session_id=session_id,
        ))
        db.commit()
        result = {"status": "ok", "tier": "credit_pack"}

        if user and user.email:
            try:
                send_purchase_confirmation(
                    user.email,
                    user.full_name or "Student",
                    f"{credits} Credit Pack",
                    f"${7.99 if credits == 10 else 14.99}",
                    "Credits never expire",
                )
            except Exception:
                pass
    else:
        result = {"status": "skipped", "reason": "unknown plan"}

    return result


@router.post("/create-checkout")
async def create_checkout(
    body: CheckoutRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    email = user.email if user else ""

    s = get_settings()
    success = body.success_url or f"{s.frontend_url}/settings?checkout=success"
    cancel = body.cancel_url or f"{s.frontend_url}/pricing"

    try:
        session = create_checkout_session(
            plan_slug=body.plan_slug,
            user_id=user_id,
            user_email=email,
            success_url=success,
            cancel_url=cancel,
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Checkout creation failed: {str(e)}")


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = handle_webhook(payload, signature)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = event["type"]
    data = event["data"]["object"]

    # === checkout.session.completed ===
    if event_type == "checkout.session.completed":
        metadata = data.get("metadata", {})
        user_id = metadata.get("user_id")
        plan_slug = metadata.get("plan_slug")
        session_id = data.get("id")
        customer_id = data.get("customer")

        if not user_id or not plan_slug:
            return {"status": "skipped", "reason": "missing metadata"}

        # Check idempotency
        if _idempotent_check(db, session_id):
            return {"status": "already_processed"}

        user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if user:
            user.stripe_customer_id = customer_id
            db.commit()

        result = _provision_plan(db, user_id, plan_slug, session_id, data.get("subscription"))
        return result

    # === invoice.paid (renewal) ===
    elif event_type == "invoice.paid":
        subscription_id = data.get("subscription")
        if not subscription_id:
            return {"status": "skipped", "reason": "no subscription id"}

        sub = db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == subscription_id,
            UserSubscription.status == "active",
        ).first()
        if not sub:
            return {"status": "skipped", "reason": "subscription not found"}

        # Extend to next billing period
        period_end = data.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
        if period_end:
            sub.current_period_start = datetime.now(timezone.utc)
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
            db.commit()
        return {"status": "ok", "renewed": True}

    # === invoice.payment_failed ===
    elif event_type == "invoice.payment_failed":
        subscription_id = data.get("subscription")
        if subscription_id:
            sub = db.query(UserSubscription).filter(
                UserSubscription.stripe_subscription_id == subscription_id
            ).first()
            if sub:
                sub.status = "past_due"
                db.commit()
        return {"status": "ok"}

    # === customer.subscription.deleted ===
    elif event_type == "customer.subscription.deleted":
        subscription_id = data.get("id")
        if subscription_id:
            sub = db.query(UserSubscription).filter(
                UserSubscription.stripe_subscription_id == subscription_id
            ).first()
            if sub:
                sub.status = "canceled"
                db.query(UserProfile).filter(UserProfile.id == sub.user_id).update({"subscription_tier": "free"})
                db.commit()
        return {"status": "ok"}

    return {"status": "unhandled_event", "type": event_type}


@router.get("/verify-session")
async def verify_checkout_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify a Stripe checkout session and provision if paid (no webhook dependency)."""
    import stripe
    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid session ID")

    if session.payment_status != "paid":
        return {"status": "pending", "message": "Payment not completed yet"}

    # Access metadata via bracket notation (avoids StripeObject .get() bug in older SDKs)
    try:
        sid_user = session["metadata"]["user_id"] if "metadata" in session and "user_id" in (session["metadata"] or {}) else None
        plan_slug = session["metadata"]["plan_slug"] if "metadata" in session and "plan_slug" in (session["metadata"] or {}) else None
    except (KeyError, TypeError):
        sid_user = None
        plan_slug = None

    if sid_user != user_id:
        raise HTTPException(status_code=403, detail="Session does not belong to this user")
    if not plan_slug:
        return {"status": "skipped", "reason": "missing metadata"}

    result = _provision_plan(db, user_id, plan_slug, session_id, session.subscription)
    return result


@router.post("/create-portal")
async def create_customer_portal(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a Stripe Customer Portal session for managing subscription."""
    import stripe
    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user or not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer found")

    try:
        portal = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
            return_url=f"{s.frontend_url}/settings",
        )
        return {"url": portal.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Portal creation failed: {str(e)}")


@router.get("/subscription")
async def get_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the user's current subscription details from Stripe."""
    import stripe
    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    sub = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id,
        UserSubscription.status.in_(["active", "past_due"]),
    ).order_by(UserSubscription.current_period_end.desc()).first()

    if not sub or not sub.stripe_subscription_id:
        return {"has_subscription": False, "status": "none"}

    try:
        stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)

        pm = None
        card = None
        if stripe_sub.default_payment_method:
            pm = stripe.PaymentMethod.retrieve(stripe_sub.default_payment_method)
            card = pm.card

        return {
            "has_subscription": True,
            "status": stripe_sub.status,
            "current_period_start": datetime.fromtimestamp(stripe_sub.current_period_start, tz=timezone.utc).isoformat(),
            "current_period_end": datetime.fromtimestamp(stripe_sub.current_period_end, tz=timezone.utc).isoformat(),
            "cancel_at_period_end": stripe_sub.cancel_at_period_end,
            "plan_name": "Premium" if (sub.plan.slug if sub.plan else "").startswith("premium") or True else sub.plan.name if sub.plan else "Premium",
            "plan_amount": stripe_sub.items.data[0].price.unit_amount / 100 if stripe_sub.items.data else 14.99,
            "plan_interval": stripe_sub.items.data[0].price.recurring.interval if stripe_sub.items.data and stripe_sub.items.data[0].price.recurring else "month",
            "card_last4": card.last4 if card else None,
            "card_brand": card.brand if card else None,
        }
    except Exception:
        return {"has_subscription": True, "status": sub.status, "error": "Could not retrieve Stripe details"}


@router.post("/cancel")
async def cancel_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel the user's subscription at period end."""
    import stripe
    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    sub = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id,
        UserSubscription.status.in_(["active", "past_due"]),
        UserSubscription.stripe_subscription_id.isnot(None),
    ).first()

    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found")

    try:
        stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
        sub.status = "active"
        db.commit()
        return {"status": "ok", "canceled_at_period_end": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cancellation failed: {str(e)}")


@router.post("/reactivate")
async def reactivate_subscription(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reactivate a subscription set to cancel at period end."""
    import stripe
    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    sub = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id,
        UserSubscription.status == "active",
        UserSubscription.stripe_subscription_id.isnot(None),
    ).first()

    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found")

    try:
        stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)
        return {"status": "ok", "canceled_at_period_end": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reactivation failed: {str(e)}")


@router.get("/invoices")
async def get_invoices(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get recent invoices for the user."""
    import stripe
    s = get_settings()
    stripe.api_key = s.stripe_secret_key

    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if not user or not user.stripe_customer_id:
        return {"invoices": []}

    try:
        invoices = stripe.Invoice.list(customer=user.stripe_customer_id, limit=6)
        return {"invoices": [{
            "id": inv.id,
            "amount_paid": inv.amount_paid / 100,
            "status": inv.status,
            "created": datetime.fromtimestamp(inv.created, tz=timezone.utc).isoformat(),
            "hosted_invoice_url": inv.hosted_invoice_url,
            "invoice_pdf": inv.invoice_pdf,
        } for inv in invoices.data if inv.status == "paid"]}
    except Exception:
        return {"invoices": []}
