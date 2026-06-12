import json
import logging
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider, SubscriptionInfo
from app.services.email_service import send_trial_welcome_email, send_purchase_confirmation

logger = logging.getLogger(__name__)


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
            if r.status_code >= 400:
                logger.error("Paddle POST %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                params=params,
            )
            if r.status_code >= 400:
                logger.error("Paddle GET %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _patch(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{self._base_url()}{path}",
                headers=self._headers(),
                json=body,
            )
            if r.status_code >= 400:
                logger.error("Paddle PATCH %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        price_id = self._price_id(plan_slug)

        body = {
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

        data = await self._post("/transactions", body)
        return {
            "url": data["data"]["checkout"]["url"],
            "transaction_id": data["data"]["id"],
        }

    # -- webhook --------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """Paddle webhooks use HMAC SHA-256 with ts=...;h1=... header format."""
        import hmac
        import hashlib

        parts = {}
        for pair in signature.split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                parts[k.strip()] = v.strip()

        ts = parts.get("ts", "")
        h1 = parts.get("h1", "")
        if not ts or not h1:
            raise ValueError("Invalid Paddle webhook signature format")

        s = get_settings()
        secret = getattr(s, "paddle_webhook_secret", "") or ""
        signed_payload = f"{ts}:{payload.decode()}"
        computed = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, h1):
            raise ValueError("Invalid Paddle webhook signature")

        return json.loads(payload)

    async def process_webhook_event(
        self, event: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        event_type = event.get("event_type", "")
        data = event.get("data", {})
        sub_id = data.get("id") or data.get("subscription_id")
        logger.info("Webhook received event_type=%s subscription_id=%s", event_type, sub_id)

        if event_type == "transaction.completed":
            return self._handle_transaction_completed(data, db, UserProfile, UserSubscription, SubscriptionPlan)

        if event_type == "subscription.canceled":
            if sub_id:
                sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == sub_id,
                ).first()
                if sub:
                    sub.status = "canceled"
                    sub.canceled_at = datetime.now(timezone.utc)
                    sub.auto_renew = False
                    db.query(UserProfile).filter(UserProfile.id == sub.user_id).update({"subscription_tier": "free"})
                    db.commit()
                    logger.info("Subscription canceled user=%s sub=%s", sub.user_id, sub_id)
                else:
                    logger.warning("Subscription not found for cancel event sub=%s", sub_id)
            return {"status": "ok"}

        if event_type == "subscription.updated":
            if sub_id:
                sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == sub_id,
                ).first()
                if sub:
                    new_status = data.get("status")
                    if sub.status == "canceled" and new_status == "active":
                        logger.warning("Skipping canceled→active transition for sub=%s (requires new payment)", sub_id)
                        return {"status": "skipped", "reason": "canceled_to_active"}
                    if new_status:
                        sub.status = new_status
                    period_end = data.get("current_billing_period", {}).get("ends_at")
                    if period_end:
                        sub.current_period_end = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
                    scheduled_change = data.get("scheduled_change")
                    if scheduled_change and scheduled_change.get("action") == "cancel":
                        sub.auto_renew = False
                    db.commit()
                    logger.info("Subscription updated user=%s sub=%s status=%s", sub.user_id, sub_id, sub.status)
                else:
                    logger.warning("Subscription not found for update event sub=%s", sub_id)
            return {"status": "ok"}

        return {"status": "unhandled_event", "type": event_type}

    # -- verify_transaction (synchronous provisioning for frontend) -----------

    async def verify_transaction(
        self, transaction_id: str, user_id: str, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        try:
            data = await self._get(f"/transactions/{transaction_id}")
        except Exception:
            return {"status": "error", "message": "Invalid transaction ID"}

        txn = data.get("data", {})
        if txn.get("status") not in ("completed", "paid"):
            return {"status": "pending", "message": "Payment not completed"}

        if db.query(UserSubscription).filter(UserSubscription.stripe_session_id == transaction_id).first():
            return {"status": "already_processed"}

        custom_data = txn.get("custom_data", {}) or {}
        txn_user_id = custom_data.get("user_id")
        txn_plan_slug = custom_data.get("plan_slug")

        if not txn_user_id or txn_user_id != user_id:
            return {"status": "error", "message": "Transaction does not match user"}

        if not txn_plan_slug:
            if custom_data.get("purpose") == "payment_method_update":
                return {"status": "ok", "note": "payment_method_updated"}
            return {"status": "error", "message": "Missing plan in transaction"}

        self._handle_transaction_completed(txn, db, UserProfile, UserSubscription, SubscriptionPlan)
        return {"status": "ok"}

    # -- transaction.completed handler ----------------------------------------

    def _handle_transaction_completed(
        self, data: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        transaction_id = data.get("id")
        subscription_id = data.get("subscription_id")
        customer_id = data.get("customer_id", "")

        if not subscription_id:
            return {"status": "skipped", "reason": "no_subscription_id"}

        # Check for duplicate transaction (idempotency)
        from app.models.subscription import UserPayment
        if db.query(UserPayment).filter(UserPayment.flow_order == transaction_id).first():
            logger.info("Duplicate transaction skipped txn=%s", transaction_id)
            return {"status": "already_processed"}

        # --- RECURRING PAYMENT: extend existing subscription ---
        existing_sub = db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == subscription_id,
        ).first()

        if existing_sub:
            custom_data = data.get("custom_data", {}) or {}
            if custom_data.get("purpose") == "payment_method_update":
                return {"status": "skipped", "reason": "payment_method_update"}

            now = datetime.now(timezone.utc)
            period_end = data.get("billing_period", {}).get("ends_at")
            existing_sub.current_period_end = (
                datetime.fromisoformat(period_end.replace("Z", "+00:00"))
                if period_end else now + timedelta(days=30)
            )
            db.flush()

            totals = data.get("details", {}).get("totals", {})
            amount_cents = int(totals.get("grand_total", 1499))
            if amount_cents > 0:
                prev_payments = db.query(UserPayment).filter(
                    UserPayment.subscription_id == existing_sub.id,
                ).count()
                is_first_charge = prev_payments == 0

                db.add(UserPayment(
                    user_id=str(existing_sub.user_id), subscription_id=existing_sub.id,
                    amount_clp=amount_cents, currency="USD",
                    flow_order=transaction_id, flow_invoice_id=transaction_id,
                    period_start=now, period_end=existing_sub.current_period_end,
                    payment_type="recurring",
                ))
                db.flush()

                if is_first_charge:
                    user = db.query(UserProfile).filter(UserProfile.id == existing_sub.user_id).first()
                    if user and user.email:
                        send_purchase_confirmation(
                            to_email=user.email,
                            name=user.full_name or "there",
                            plan_name="Pro Monthly",
                            amount=f"${amount_cents / 100:.2f}/month",
                            period=f"Next billing: {existing_sub.current_period_end.strftime('%B %d, %Y')}",
                        )

            logger.info("Subscription renewed user=%s sub=%s amount_cents=%s txn=%s",
                        existing_sub.user_id, subscription_id, amount_cents, transaction_id)
            db.commit()
            return {"status": "renewed", "subscription_id": subscription_id}

        # --- NEW SUBSCRIPTION ---
        custom_data = data.get("custom_data", {}) or {}
        user_id = custom_data.get("user_id")
        plan_slug = custom_data.get("plan_slug")

        if not user_id or not plan_slug:
            return {"status": "skipped", "reason": "missing_custom_data"}

        # Idempotency: check if already processed
        if db.query(UserSubscription).filter(UserSubscription.stripe_session_id == transaction_id).first():
            logger.info("Duplicate transaction skipped (new sub) txn=%s", transaction_id)
            return {"status": "already_processed"}

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
        if not plan or plan_slug not in ("premium", "exam_week_pass"):
            return {"status": "skipped"}

        now = datetime.now(timezone.utc)
        period_end = data.get("billing_period", {}).get("ends_at")
        current_period_end = (
            datetime.fromisoformat(period_end.replace("Z", "+00:00"))
            if period_end else now + timedelta(days=30)
        )

        new_sub = UserSubscription(
            id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
            status="active", current_period_start=now, current_period_end=current_period_end,
            stripe_subscription_id=subscription_id, stripe_session_id=transaction_id,
        )
        db.add(new_sub)

        update = {"subscription_tier": "premium"}
        if customer_id:
            update["stripe_customer_id"] = customer_id
        db.query(UserProfile).filter(UserProfile.id == user_id).update(update)
        db.flush()

        totals = data.get("details", {}).get("totals", {})
        amount_cents = int(totals.get("grand_total", 0))
        if amount_cents > 0:
            db.add(UserPayment(
                user_id=user_id, subscription_id=new_sub.id,
                amount_clp=amount_cents, currency="USD",
                flow_order=transaction_id, flow_invoice_id=transaction_id,
                period_start=now, period_end=current_period_end,
                payment_type="first_charge",
            ))

        if amount_cents == 0:
            user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
            if user and user.email:
                send_trial_welcome_email(
                    to_email=user.email,
                    name=user.full_name or "there",
                )

        logger.info("Subscription created user=%s sub=%s plan=%s txn=%s amount_cents=%s",
                    user_id, subscription_id, plan_slug, transaction_id, amount_cents)
        db.commit()
        return {"status": "ok"}

    # -- get_subscription -----------------------------------------------------

    async def get_subscription(self, user_id: str, db: DbSession, UserSubscription) -> SubscriptionInfo:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if not sub:
            return SubscriptionInfo(has_subscription=False)

        plan = sub.plan
        is_one_time = not sub.stripe_subscription_id

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
            cancel_at_period_end=not sub.auto_renew,
            plan_name=plan.name if plan else ("Week Pass" if is_one_time else "Premium"),
            plan_slug=plan.slug if plan else ("exam_week_pass" if is_one_time else "premium"),
            plan_amount=plan.price_cents / 100 if plan else (4.99 if is_one_time else 14.99),
            plan_interval=plan.interval if plan else ("week" if is_one_time else "month"),
            card_last4=card_last4,
            card_brand=card_brand,
        )

    # -- subscription management ----------------------------------------------

    async def cancel_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing"]),
        ).first()
        if not sub:
            raise ValueError("No active subscription found")

        if sub.stripe_subscription_id:
            try:
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "scheduled_change": {"action": "cancel", "effective_at": "next_billing_period"},
                })
            except Exception:
                logger.exception("Paddle cancel subscription failed")

        sub.status = "cancel_at_period_end"
        sub.auto_renew = False
        db.commit()
        logger.info("Subscription canceled user=%s sub=%s", user_id, sub.stripe_subscription_id)
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
                logger.exception("Paddle reactivate subscription failed")

        sub.auto_renew = True
        db.commit()
        return {"status": "ok"}

    async def switch_plan(
        self, new_plan_slug: str, user_id: str, user_email: str,
        frontend_url: str, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        s = get_settings()

        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if not sub:
            raise ValueError("No active subscription found")

        user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
        if not user:
            raise ValueError("User not found")

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

        if sub.stripe_subscription_id:
            try:
                new_price_id = self._price_id(new_plan_slug)
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "items": [{"price_id": new_price_id, "quantity": 1}],
                    "proration_billing_mode": "prorated_immediately",
                })
            except Exception:
                logger.exception("Paddle switch plan failed")

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == new_plan_slug).first()
        if plan:
            sub.plan_id = str(plan.id)
        db.commit()

        return {"status": "ok", "plan": new_plan_slug}

    async def create_portal(self, user_id: str, db: DbSession, UserProfile) -> dict:
        s = get_settings()

        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing"]),
            UserSubscription.stripe_subscription_id.isnot(None),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if sub and sub.stripe_subscription_id:
            try:
                data = await self._post(
                    f"/subscriptions/{sub.stripe_subscription_id}/update-payment-method-transaction",
                    {
                        "custom_data": {
                            "user_id": user_id,
                            "purpose": "payment_method_update",
                        },
                    },
                )
                txn_id = data["data"]["id"]
                return {"transaction_id": txn_id}
            except Exception:
                logger.exception("Failed to create Paddle payment method update transaction")

        return {"url": f"{s.frontend_url}/settings"}

    async def get_invoices(self, user_id: str, db: DbSession, UserProfile) -> list[dict]:
        from app.models.subscription import UserPayment

        payments = db.query(UserPayment).filter(
            UserPayment.user_id == user_id,
        ).order_by(UserPayment.created_at.desc()).limit(12).all()

        return [{
            "id": str(p.id),
            "amount_paid": round(p.amount_clp / 100, 2),
            "status": p.status,
            "created": p.created_at.isoformat() if p.created_at else "",
            "hosted_invoice_url": None,
            "invoice_pdf": None,
            "payment_type": p.payment_type,
            "paddle_transaction_id": p.flow_order,
        } for p in payments]
