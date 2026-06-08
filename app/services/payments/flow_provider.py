# ============================================================
# Flow.cl Payment Provider (Chile)
# API: https://www.flow.cl/docs/api.html
# ============================================================

import hashlib
import hmac
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, parse_qs
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider, SubscriptionInfo

logger = logging.getLogger(__name__)


class FlowProvider(PaymentProvider):

    @property
    def provider_name(self) -> str:
        return "flow"

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _base_url() -> str:
        s = get_settings()
        env = getattr(s, "flow_environment", "sandbox") or "sandbox"
        return "https://sandbox.flow.cl/api" if env == "sandbox" else "https://www.flow.cl/api"

    @staticmethod
    def _credentials() -> tuple[str, str]:
        s = get_settings()
        return s.flow_api_key, s.flow_secret_key

    def _sign(self, params: dict) -> str:
        _, secret = self._credentials()
        filtered = {k: v for k, v in sorted(params.items()) if k != "s"}
        to_sign = urlencode(filtered)
        return hmac.new(secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

    def _verify_signature(self, params: dict) -> bool:
        received_sig = params.pop("s", None)
        if not received_sig:
            return False
        computed = self._sign(dict(sorted(params.items())))
        return hmac.compare_digest(computed, received_sig)

    async def _post(self, path: str, params: dict) -> dict:
        api_key, _ = self._credentials()
        params["apiKey"] = api_key
        params["s"] = self._sign(params)
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{self._base_url()}{path}", data=params)
            r.raise_for_status()
            return r.json()

    def _amount_clp(self, plan_slug: str, is_trial: bool = False) -> int:
        if plan_slug == "premium":
            return 2990 if is_trial else 14990
        raise ValueError(f"No Flow price configured for: {plan_slug}")

    @staticmethod
    def _plan_label(plan_slug: str) -> str:
        labels = {
            "premium": "Premium Mensual",
        }
        return labels.get(plan_slug, plan_slug)

    def _commerce_order(self, user_id: str, plan_slug: str) -> str:
        return f"{user_id}:{plan_slug}:{uuid4().hex[:6]}"

    def _parse_commerce_order(self, commerce_order: str) -> tuple[str, str] | None:
        try:
            parts = commerce_order.split(":")
            if len(parts) >= 2:
                return parts[0], parts[1]
            return None
        except Exception:
            return None

    def _confirmation_url(self) -> str:
        s = get_settings()
        backend = getattr(s, "backend_url", None) or s.frontend_url
        return f"{backend}/api/v1/payments/webhook"

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        commerce_order = self._commerce_order(user_id, plan_slug)
        confirm_url = self._confirmation_url()

        if plan_slug == "premium":
            params = {
                "planName": self._plan_label(plan_slug),
                "planAmount": self._amount_clp(plan_slug),
                "planPeriod": "month",
                "planCurrency": "clp",
                "planTrialPeriod": 7,
                "planTrialAmount": self._amount_clp(plan_slug, is_trial=True),
                "commerceOrder": commerce_order,
                "email": user_email,
                "urlConfirmation": confirm_url,
                "urlReturn": success_url,
            }
            try:
                data = await self._post("/subscription/create", params)
                return {"url": data["url"]}
            except Exception:
                logger.exception("Flow subscription creation failed")
                raise
        else:
            params = {
                "commerceOrder": commerce_order,
                "subject": f"Bandami - {self._plan_label(plan_slug)}",
                "amount": self._amount_clp(plan_slug),
                "currency": "clp",
                "email": user_email,
                "urlConfirmation": confirm_url,
                "urlReturn": success_url,
            }
            try:
                data = await self._post("/payment/create", params)
                return {"url": data["url"]}
            except Exception:
                logger.exception("Flow payment creation failed")
                raise

    # -- webhook --------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        parsed = parse_qs(payload.decode())
        params = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        if not self._verify_signature(params):
            raise ValueError("Invalid Flow webhook signature")
        return params

    async def process_webhook_event(
        self, event: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        token = event.get("token", "")
        flow_order = str(event.get("flowOrder", ""))

        if not token:
            return {"status": "skipped", "reason": "missing_token"}

        try:
            status_data = await self._post("/payment/getStatus", {"token": token})
        except Exception:
            logger.exception("Failed to get payment status from Flow")
            return {"status": "failed", "reason": "api_error"}

        if status_data.get("status") != "EXITO":
            return {"status": "pending", "message": "payment_not_completed"}

        if db.query(UserSubscription).filter(UserSubscription.stripe_session_id == flow_order).first():
            return {"status": "already_processed"}

        commerce_order = status_data.get("commerceOrder", "")
        parsed = self._parse_commerce_order(commerce_order)
        if not parsed:
            return {"status": "skipped", "reason": "invalid_commerce_order"}

        user_id, plan_slug = parsed

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
        if not plan or plan_slug not in ("premium", "exam_week_pass", "credit_pack_10", "credit_pack_25"):
            return {"status": "skipped", "reason": "invalid_plan"}

        subscription_id = status_data.get("subscriptionId")
        now = datetime.now(timezone.utc)

        # If recurring payment for an existing subscription → extend period
        if subscription_id:
            existing = db.query(UserSubscription).filter(
                UserSubscription.stripe_subscription_id == subscription_id,
                UserSubscription.status == "active",
            ).first()
            if existing:
                existing.current_period_end = now + timedelta(days=30)
                existing.stripe_session_id = flow_order
                db.commit()
                return {"status": "renewed", "plan": plan_slug, "user_id": user_id}

        days = 30 if plan_slug == "premium" else (7 if plan_slug == "exam_week_pass" else 365)

        new_sub = UserSubscription(
            id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
            status="active", current_period_start=now,
            current_period_end=now + timedelta(days=days),
            stripe_subscription_id=subscription_id,
            stripe_session_id=flow_order,
        )
        db.add(new_sub)
        db.query(UserProfile).filter(UserProfile.id == user_id).update({"subscription_tier": "premium"})
        db.commit()

        return {"status": "ok", "plan": plan_slug, "user_id": user_id}

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

        return SubscriptionInfo(
            has_subscription=True,
            is_one_time=is_one_time,
            status=sub.status,
            current_period_start=sub.current_period_start.isoformat() if sub.current_period_start else None,
            current_period_end=sub.current_period_end.isoformat() if sub.current_period_end else None,
            cancel_at_period_end=False,
            plan_name=plan.name if plan else ("Week Pass" if is_one_time else "Premium"),
            plan_slug=plan.slug if plan else ("exam_week_pass" if is_one_time else "premium"),
            plan_amount=plan.price_cents / 100 if plan else (2.99 if is_one_time else 14.99),
            plan_interval=plan.interval if plan else ("week" if is_one_time else "month"),
            card_last4=None,
            card_brand=None,
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
                await self._post("/subscription/cancel", {
                    "subscriptionId": sub.stripe_subscription_id,
                })
            except Exception:
                logger.exception("Flow cancel subscription failed")

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

        if not sub.stripe_subscription_id:
            raise ValueError("Cannot reactivate one-time purchase")

        raise ValueError(
            "Flow does not support reactivation. Please purchase a new subscription."
        )

    async def switch_plan(
        self, new_plan_slug: str, user_id: str, user_email: str,
        frontend_url: str, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if not sub:
            raise ValueError("No active subscription found")

        is_one_time = not sub.stripe_subscription_id

        if is_one_time and new_plan_slug == "premium":
            result = await self.create_checkout(
                plan_slug="premium", user_id=user_id, user_email=user_email,
                success_url=f"{frontend_url}/settings?checkout=success",
                cancel_url=f"{frontend_url}/settings",
            )
            return {"status": "redirect_to_checkout", "url": result["url"]}

        if is_one_time:
            raise ValueError("Current plan cannot be switched")

        result = await self.create_checkout(
            plan_slug=new_plan_slug, user_id=user_id, user_email=user_email,
            success_url=f"{frontend_url}/settings?checkout=success",
            cancel_url=f"{frontend_url}/settings",
        )
        return {"status": "redirect_to_checkout", "url": result["url"]}

    async def create_portal(self, user_id: str, db: DbSession, UserProfile) -> dict:
        s = get_settings()
        return {"url": f"{s.frontend_url}/settings"}

    async def get_invoices(self, user_id: str, db: DbSession, UserProfile) -> list[dict]:
        from app.models.subscription import UserSubscription

        subs = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
        ).order_by(UserSubscription.created_at.desc()).limit(6).all()

        return [{
            "id": str(sub.id),
            "amount_paid": float(sub.plan.price_cents / 100) if sub.plan else 0,
            "status": sub.status,
            "created": sub.created_at.isoformat() if sub.created_at else "",
            "hosted_invoice_url": None,
            "invoice_pdf": None,
        } for sub in subs]
