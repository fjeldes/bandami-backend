# ============================================================
# Stripe Payment Provider
# ============================================================

import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider, SubscriptionInfo


class StripeProvider(PaymentProvider):

    @property
    def provider_name(self) -> str:
        return "stripe"

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _stripe():
        import stripe
        s = get_settings()
        stripe.api_key = s.stripe_secret_key
        return stripe

    def _price_id(self, plan_slug: str) -> str:
        s = get_settings()
        mapping = {
            "premium": s.stripe_price_premium,
            "exam_week_pass": s.stripe_price_exam_week,
        }
        pid = mapping.get(plan_slug)
        if not pid:
            raise ValueError(f"No Stripe price configured for: {plan_slug}")
        return pid

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        stripe = self._stripe()
        price_id = self._price_id(plan_slug)
        mode = "subscription" if plan_slug == "premium" else "payment"

        config: dict = {
            "customer_email": user_email,
            "mode": mode,
            "metadata": {"user_id": user_id, "plan_slug": plan_slug},
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url + "&session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url,
            "allow_promotion_codes": True,
        }

        if discount_percent > 0:
            coupon = stripe.Coupon.create(
                percent_off=discount_percent, duration="once", max_redemptions=1,
                metadata={"user_id": user_id},
            )
            config["discounts"] = [{"coupon": coupon.id}]

        session = stripe.checkout.Session.create(**config)
        return {"url": session.url}

    # -- webhook --------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        stripe = self._stripe()
        s = get_settings()
        return stripe.Webhook.construct_event(payload, signature, s.stripe_webhook_secret)

    async def process_webhook_event(self, event: dict, db: DbSession, UserProfile, UserSubscription, SubscriptionPlan) -> dict:
        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "checkout.session.completed":
            return _handle_checkout_completed(data, db, UserProfile, UserSubscription, SubscriptionPlan)

        elif event_type == "invoice.paid":
            sub_id = data.get("subscription")
            if sub_id:
                sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == sub_id,
                    UserSubscription.status == "active",
                ).first()
                if sub:
                    period_end = data.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
                    if period_end:
                        sub.current_period_start = datetime.now(timezone.utc)
                        sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
                        db.commit()
            return {"status": "ok"}

        elif event_type == "invoice.payment_failed":
            sub_id = data.get("subscription")
            if sub_id:
                sub = db.query(UserSubscription).filter(UserSubscription.stripe_subscription_id == sub_id).first()
                if sub:
                    sub.status = "past_due"
                    db.commit()
            return {"status": "ok"}

        elif event_type == "customer.subscription.deleted":
            sub_id = data.get("id")
            if sub_id:
                sub = db.query(UserSubscription).filter(UserSubscription.stripe_subscription_id == sub_id).first()
                if sub:
                    sub.status = "canceled"
                    db.query(UserProfile).filter(UserProfile.id == sub.user_id).update({"subscription_tier": "free"})
                    db.commit()
            return {"status": "ok"}

        elif event_type == "customer.subscription.updated":
            sub_id = data.get("id")
            if sub_id:
                sub = db.query(UserSubscription).filter(UserSubscription.stripe_subscription_id == sub_id).first()
                if sub:
                    if data.get("status"):
                        sub.status = data["status"]
                    if data.get("current_period_end"):
                        sub.current_period_end = datetime.fromtimestamp(data["current_period_end"], tz=timezone.utc)
                    if data.get("current_period_start"):
                        sub.current_period_start = datetime.fromtimestamp(data["current_period_start"], tz=timezone.utc)
                    s = get_settings()
                    if data.get("items", {}).get("data"):
                        new_price_id = data["items"]["data"][0].get("price", {}).get("id")
                        if new_price_id:
                            plan_map = {s.stripe_price_premium: "premium", s.stripe_price_exam_week: "exam_week_pass"}
                            new_slug = plan_map.get(new_price_id)
                            if new_slug:
                                plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == new_slug).first()
                                if plan:
                                    sub.plan_id = str(plan.id)
                    db.commit()
            return {"status": "ok"}

        return {"status": "unhandled_event", "type": event_type}

    # -- get_subscription -----------------------------------------------------

    async def get_subscription(self, user_id: str, db: DbSession, UserSubscription) -> SubscriptionInfo:
        stripe = self._stripe()
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if not sub:
            return SubscriptionInfo(has_subscription=False)

        if not sub.stripe_subscription_id:
            plan = sub.plan
            return SubscriptionInfo(
                has_subscription=True, is_one_time=True, status=sub.status,
                current_period_start=sub.current_period_start.isoformat(),
                current_period_end=sub.current_period_end.isoformat(),
                plan_name=plan.name if plan else "Week Pass",
                plan_slug=plan.slug if plan else "exam_week_pass",
                plan_amount=4.99, plan_interval="week",
            )

        try:
            stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
            card = None
            if stripe_sub.default_payment_method:
                pm = stripe.PaymentMethod.retrieve(stripe_sub.default_payment_method)
                card = pm.card

            return SubscriptionInfo(
                has_subscription=True, is_one_time=False, status=stripe_sub.status,
                current_period_start=_dt(stripe_sub.current_period_start),
                current_period_end=_dt(stripe_sub.current_period_end),
                cancel_at_period_end=stripe_sub.cancel_at_period_end,
                plan_name=sub.plan.name if sub.plan else "Premium",
                plan_slug=sub.plan.slug if sub.plan else "premium",
                plan_amount=stripe_sub.items.data[0].price.unit_amount / 100 if stripe_sub.items.data else 14.99,
                plan_interval=stripe_sub.items.data[0].price.recurring.interval if stripe_sub.items.data and stripe_sub.items.data[0].price.recurring else "month",
                card_last4=card.last4 if card else None,
                card_brand=card.brand if card else None,
            )
        except Exception:
            return SubscriptionInfo(has_subscription=True, status=sub.status)

    # -- subscription management ----------------------------------------------

    async def cancel_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        stripe = self._stripe()
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due"]),
            UserSubscription.stripe_subscription_id.isnot(None),
        ).first()
        if not sub:
            raise ValueError("No active subscription found")
        stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
        sub.status = "active"
        db.commit()
        return {"status": "ok", "canceled_at_period_end": True}

    async def reactivate_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        stripe = self._stripe()
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.stripe_subscription_id.isnot(None),
        ).first()
        if not sub:
            raise ValueError("No active subscription found")
        stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)
        return {"status": "ok"}

    async def switch_plan(self, new_plan_slug: str, user_id: str, user_email: str, frontend_url: str, db: DbSession, UserProfile, UserSubscription, SubscriptionPlan) -> dict:
        stripe = self._stripe()
        s = get_settings()

        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if not sub:
            raise ValueError("No active subscription found")

        user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        if not sub.stripe_subscription_id and new_plan_slug == "premium":
            result = await self.create_checkout(
                plan_slug="premium", user_id=user_id, user_email=user_email,
                success_url=f"{s.frontend_url}/settings?checkout=success",
                cancel_url=f"{s.frontend_url}/settings",
            )
            return {"status": "redirect_to_checkout", "url": result["url"]}

        if not sub.stripe_subscription_id:
            raise ValueError("Current plan cannot be switched")

        if not user.stripe_customer_id:
            raise ValueError("No Stripe customer found")

        new_price_id = self._price_id(new_plan_slug)
        stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
        item_id = stripe_sub["items"].data[0].id

        updated = stripe.Subscription.modify(
            sub.stripe_subscription_id,
            items=[{"id": item_id, "price": new_price_id}],
            proration_behavior="create_prorations",
        )

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == new_plan_slug).first()
        if plan:
            sub.plan_id = str(plan.id)
        sub.current_period_end = datetime.fromtimestamp(updated.current_period_end, tz=timezone.utc)
        db.commit()

        return {"status": "ok", "plan": new_plan_slug}

    async def create_portal(self, user_id: str, db: DbSession, UserProfile) -> dict:
        stripe = self._stripe()
        s = get_settings()

        user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        if not user.stripe_customer_id:
            customers = stripe.Customer.list(email=user.email, limit=1)
            if customers.data:
                user.stripe_customer_id = customers.data[0].id
            else:
                c = stripe.Customer.create(email=user.email, metadata={"user_id": user_id})
                user.stripe_customer_id = c.id
            db.commit()

        portal = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
            return_url=f"{s.frontend_url}/settings",
        )
        return {"url": portal.url}

    async def get_invoices(self, user_id: str, db: DbSession, UserProfile) -> list[dict]:
        stripe = self._stripe()
        user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not user or not user.stripe_customer_id:
            return []

        invoices = stripe.Invoice.list(customer=user.stripe_customer_id, limit=6)
        return [{
            "id": inv.id, "amount_paid": inv.amount_paid / 100, "status": inv.status,
            "created": datetime.fromtimestamp(inv.created, tz=timezone.utc).isoformat(),
            "hosted_invoice_url": inv.hosted_invoice_url, "invoice_pdf": inv.invoice_pdf,
        } for inv in invoices.data if inv.status == "paid"]


# -- module-level helpers ----------------------------------------------------

def _handle_checkout_completed(data, db, UserProfile, UserSubscription, SubscriptionPlan) -> dict:
    metadata = data.get("metadata", {})
    user_id = metadata.get("user_id")
    plan_slug = metadata.get("plan_slug")
    session_id = data.get("id")
    customer_id = data.get("customer")

    if not user_id or not plan_slug:
        return {"status": "skipped", "reason": "missing metadata"}

    if db.query(UserSubscription).filter(UserSubscription.stripe_session_id == session_id).first():
        return {"status": "already_processed"}

    user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
    if user:
        user.stripe_customer_id = customer_id
        db.commit()

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
    if not plan or plan_slug not in ("premium", "exam_week_pass"):
        return {"status": "skipped"}

    now = datetime.now(timezone.utc)
    days = 30 if plan_slug == "premium" else 7
    subscription_id = data.get("subscription")

    db.add(UserSubscription(
        id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
        status="active", current_period_start=now, current_period_end=now + timedelta(days=days),
        stripe_subscription_id=subscription_id, stripe_session_id=session_id,
    ))
    db.query(UserProfile).filter(UserProfile.id == user_id).update({"subscription_tier": "premium"})
    db.commit()
    return {"status": "ok"}


def _dt(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
