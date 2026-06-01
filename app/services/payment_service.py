"""
Stripe payment service — checkout sessions and webhook handling.
"""

import stripe
from app.core.config import get_settings

settings = get_settings()
stripe.api_key = settings.stripe_secret_key

PRICE_MAP = {
    "premium": settings.stripe_price_premium,
    "credit_pack_10": settings.stripe_price_credit_10,
    "credit_pack_25": settings.stripe_price_credit_25,
    "exam_week_pass": settings.stripe_price_exam_week,
}


def create_checkout_session(
    plan_slug: str,
    user_id: str,
    user_email: str,
    success_url: str,
    cancel_url: str,
) -> stripe.checkout.Session:
    price_id = PRICE_MAP.get(plan_slug)
    if not price_id:
        raise ValueError(f"No Stripe price configured for plan: {plan_slug}")

    metadata: dict[str, str] = {
        "user_id": user_id,
        "plan_slug": plan_slug,
    }

    mode = "subscription" if plan_slug == "premium" else "payment"

    session = stripe.checkout.Session.create(
        customer_email=user_email,
        mode=mode,
        metadata=metadata,
        line_items=[{
            "price": price_id,
            "quantity": 1,
        }],
        success_url=success_url + "&session_id={CHECKOUT_SESSION_ID}",
        cancel_url=cancel_url,
        allow_promotion_codes=True,
    )

    return session


def handle_webhook(payload: bytes, signature: str) -> dict:
    event = stripe.Webhook.construct_event(
        payload, signature, settings.stripe_webhook_secret
    )
    return event


def get_or_create_customer(email: str, user_id: str) -> stripe.Customer:
    customers = stripe.Customer.list(email=email, limit=1)
    if customers.data:
        return customers.data[0]

    return stripe.Customer.create(
        email=email,
        metadata={"user_id": user_id},
    )
