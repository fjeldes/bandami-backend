# ============================================================
# Paddle Payment Provider
# Implements PaymentProvider ABC.
# Paddle Billing API — https://developer.paddle.com
# ============================================================

import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider, SubscriptionInfo


class PaddleProvider(PaymentProvider):

    @property
    def provider_name(self) -> str:
        return "paddle"

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _base_url() -> str:
        s = get_settings()
        env = getattr(s, "paddle_environment", "sandbox") or "sandbox"
        return "https://sandbox-api.paddle.com" if env == "sandbox" else "https://api.paddle.com"

    @staticmethod
    def _headers() -> dict:
        s = get_settings()
        return {
            "Authorization": f"Bearer {s.paddle_api_key}",
            "Content-Type": "application/json",
        }

    def _price_id(self, plan_slug: str) -> str:
        s = get_settings()
        mapping = {
            "premium": s.paddle_price_premium,
            "exam_week_pass": s.paddle_price_exam_week,
        }
        pid = mapping.get(plan_slug)
        if not pid:
            raise ValueError(f"No Paddle price configured for: {plan_slug}")
        return pid

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            return r.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def _patch(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                json=body,
            )
            r.raise_for_status()
            return r.json()

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        price_id = self._price_id(plan_slug)

        body: dict = {
            "items": [{"price_id": price_id, "quantity": 1}],
            "customer": {"email": user_email},
            "custom_data": {"user_id": user_id, "plan_slug": plan_slug},
            "checkout": {
                "urls": {
                    "success": success_url,
                    "return": cancel_url,
                },
            },
        }

        # Paddle doesn't support discount_percent natively; skip for now
        # Can be added via Paddle coupons/discounts API later

        data = await self._post("/transactions", body)
        return {"url": data["data"]["checkout"]["url"]}

    # -- webhook --------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """Paddle webhooks use HMAC SHA-256 verification."""
        import hmac
        import hashlib

        s = get_settings()
        secret = getattr(s, "paddle_webhook_secret", "") or ""
        computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, signature):
            raise ValueError("Invalid webhook signature")

        return json.loads(payload)

    async def process_webhook_event(
        self, event: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        event_type = event.get("event_type", "")
        data = event.get("data", {})

        if event_type == "transaction.completed":
            return _paddle_handle_transaction_completed(data, db, UserProfile, UserSubscription, SubscriptionPlan)

        elif event_type == "subscription.canceled":
            sub_id = data.get("id")
            if sub_id:
                sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == sub_id,
                ).first()
                if sub:
                    sub.status = "canceled"
                    db.query(UserProfile).filter(UserProfile.id == sub.user_id).update({"subscription_tier": "free"})
                    db.commit()
            return {"status": "ok"}

        elif event_type == "subscription.updated":
            sub_id = data.get("id")
            if sub_id:
                sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == sub_id,
                ).first()
                if sub:
                    sub.status = data.get("status", sub.status)
                    period_end = data.get("current_billing_period", {}).get("ends_at")
                    if period_end:
                        sub.current_period_end = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
                    db.commit()
            return {"status": "ok"}

        return {"status": "unhandled_event", "type": event_type}

    # -- get_subscription -----------------------------------------------------

    async def get_subscription(self, user_id: str, db: DbSession, UserSubscription) -> SubscriptionInfo:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if not sub:
            return SubscriptionInfo(has_subscription=False)

        plan = sub.plan
        is_one_time = not sub.stripe_subscription_id

        # For Paddle subscriptions, try to fetch from API
        card_last4 = None
        card_brand = None
        if sub.stripe_subscription_id and not is_one_time:
            try:
                data = await self._get(f"/subscriptions/{sub.stripe_subscription_id}")
                sub_data = data.get("data", {})
                method = sub_data.get("payment_method", {})
                card = method.get("card", {}) if method else {}
                card_last4 = card.get("last4")
                card_brand = card.get("type")
            except Exception:
                pass

        return SubscriptionInfo(
            has_subscription=True,
            is_one_time=is_one_time,
            status=sub.status,
            current_period_start=sub.current_period_start.isoformat() if sub.current_period_start else None,
            current_period_end=sub.current_period_end.isoformat() if sub.current_period_end else None,
            cancel_at_period_end=False,
            plan_name=plan.name if plan else ("Week Pass" if is_one_time else "Premium"),
            plan_slug=plan.slug if plan else ("exam_week_pass" if is_one_time else "premium"),
            plan_amount=4.99 if is_one_time else 14.99,
            plan_interval="week" if is_one_time else "month",
            card_last4=card_last4,
            card_brand=card_brand,
        )

    # -- subscription management ----------------------------------------------

    async def cancel_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due"]),
        ).first()
        if not sub:
            raise ValueError("No active subscription found")

        if sub.stripe_subscription_id:
            try:
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "scheduled_change": {"action": "cancel", "effective_at": "next_billing_period"},
                })
            except Exception:
                pass

        sub.status = "active"
        db.commit()
        return {"status": "ok", "canceled_at_period_end": True}

    async def reactivate_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
        ).first()
        if not sub:
            raise ValueError("No active subscription found")

        if sub.stripe_subscription_id:
            try:
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "scheduled_change": None,
                })
            except Exception:
                pass

        return {"status": "ok"}

    async def switch_plan(self, new_plan_slug: str, user_id: str, user_email: str, frontend_url: str, db: DbSession, UserProfile, UserSubscription, SubscriptionPlan) -> dict:
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

        # One-time → subscription: redirect to checkout
        is_one_time = not sub.stripe_subscription_id
        if is_one_time and new_plan_slug == "premium":
            result = await self.create_checkout(
                plan_slug="premium", user_id=user_id, user_email=user_email,
                success_url=f"{s.frontend_url}/settings?checkout=success",
                cancel_url=f"{s.frontend_url}/settings",
            )
            return {"status": "redirect_to_checkout", "url": result["url"]}

        if is_one_time:
            raise ValueError("Current plan cannot be switched")

        # Subscription → subscription: update via Paddle API
        if sub.stripe_subscription_id:
            try:
                new_price_id = self._price_id(new_plan_slug)
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "items": [{"price_id": new_price_id, "quantity": 1}],
                    "proration_billing_mode": "prorated_immediately",
                })
            except Exception:
                pass

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == new_plan_slug).first()
        if plan:
            sub.plan_id = str(plan.id)
        db.commit()

        return {"status": "ok", "plan": new_plan_slug}

    async def create_portal(self, user_id: str, db: DbSession, UserProfile) -> dict:
        # Paddle doesn't have a hosted portal like Stripe. Return self-serve page.
        s = get_settings()
        return {"url": f"{s.frontend_url}/settings"}

    async def get_invoices(self, user_id: str, db: DbSession, UserProfile) -> list[dict]:
        user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not user:
            return []

        try:
            data = await self._get("/transactions", {"customer_id": user.email, "status": "completed"})
            return [{
                "id": t["id"],
                "amount_paid": float(t.get("details", {}).get("totals", {}).get("grand_total", 0)) / 100,
                "status": t.get("status", "paid"),
                "created": t.get("created_at", ""),
                "hosted_invoice_url": None,
                "invoice_pdf": None,
            } for t in data.get("data", [])[:6]]
        except Exception:
            return []


# -- module-level helpers ----------------------------------------------------

def _paddle_handle_transaction_completed(data, db, UserProfile, UserSubscription, SubscriptionPlan) -> dict:
    custom_data = data.get("custom_data", {}) or {}
    user_id = custom_data.get("user_id")
    plan_slug = custom_data.get("plan_slug")
    transaction_id = data.get("id")

    if not user_id or not plan_slug:
        return {"status": "skipped", "reason": "missing custom_data"}

    if db.query(UserSubscription).filter(UserSubscription.stripe_session_id == transaction_id).first():
        return {"status": "already_processed"}

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
    if not plan or plan_slug not in ("premium", "exam_week_pass"):
        return {"status": "skipped"}

    now = datetime.now(timezone.utc)
    days = 30 if plan_slug == "premium" else 7
    subscription_id = data.get("subscription_id")

    db.add(UserSubscription(
        id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
        status="active", current_period_start=now, current_period_end=now + timedelta(days=days),
        stripe_subscription_id=subscription_id, stripe_session_id=transaction_id,
    ))
    db.query(UserProfile).filter(UserProfile.id == user_id).update({"subscription_tier": "premium"})
    db.commit()
    return {"status": "ok"}
