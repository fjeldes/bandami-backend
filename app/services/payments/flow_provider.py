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

    FLOW_PLAN_ID = "premium_bandami"

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

    async def _get(self, path: str, params: dict) -> dict:
        api_key, _ = self._credentials()
        params["apiKey"] = api_key
        params["s"] = self._sign(params)
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self._base_url()}{path}", params=params)
            r.raise_for_status()
            return r.json()

    def _amount_clp(self, plan_slug: str, is_trial: bool = False) -> int:
        if plan_slug == "premium":
            return 2990 if is_trial else 14990
        raise ValueError(f"No Flow price configured for: {plan_slug}")

    @staticmethod
    def _plan_label(plan_slug: str) -> str:
        labels = {"premium": "Premium Mensual"}
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

    def _backend_url(self) -> str:
        s = get_settings()
        return getattr(s, "backend_url", None) or s.frontend_url

    def _card_callback_url(self, user_id: str, plan_slug: str, success_url: str, cancel_url: str) -> str:
        backend = self._backend_url()
        import json
        payload = json.dumps({"su": success_url, "ca": cancel_url})
        params = urlencode({
            "user_id": user_id,
            "plan_slug": plan_slug,
            "ctx": payload,
        })
        return f"{backend}/api/v1/payments/flow/card-callback?{params}"

    # -- Plan management ------------------------------------------------------

    async def _ensure_plan_exists(self) -> None:
        params = {
            "planId": self.FLOW_PLAN_ID,
            "name": "Premium Mensual",
            "currency": "CLP",
            "amount": self._amount_clp("premium"),
            "interval": 3,
            "interval_count": 1,
            "trial_period_days": 7,
            "urlCallback": self._confirmation_url(),
        }
        try:
            await self._post("/plans/create", params)
        except httpx.HTTPStatusError:
            logger.info("Plan may already exist in Flow, continuing")

    # -- Customer management --------------------------------------------------

    async def _create_flow_customer(self, user_id: str, email: str, name: str = "") -> str:
        params = {
            "name": name or email.split("@")[0],
            "email": email,
            "externalId": user_id,
        }
        data = await self._post("/customer/create", params)
        return data["customerId"]

    # -- Card registration ----------------------------------------------------

    async def _register_card(self, customer_id: str, url_return: str) -> dict:
        params = {
            "customerId": customer_id,
            "url_return": url_return,
        }
        return await self._post("/customer/register", params)

    async def _get_register_status(self, token: str) -> dict:
        return await self._get("/customer/getRegisterStatus", {"token": token})

    # -- Subscription management ----------------------------------------------

    async def _create_subscription(self, customer_id: str) -> dict:
        params = {
            "planId": self.FLOW_PLAN_ID,
            "customerId": customer_id,
            "trial_period_days": 7,
        }
        return await self._post("/subscription/create", params)

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        if plan_slug == "premium":
            return await self._subscription_checkout(user_id, user_email, success_url, cancel_url)

        commerce_order = self._commerce_order(user_id, plan_slug)
        params = {
            "commerceOrder": commerce_order,
            "subject": f"Bandami - {self._plan_label(plan_slug)}",
            "amount": self._amount_clp(plan_slug),
            "currency": "clp",
            "email": user_email,
            "urlConfirmation": self._confirmation_url(),
            "urlReturn": success_url,
        }
        data = await self._post("/payment/create", params)
        return {"url": data["url"]}

    async def _subscription_checkout(self, user_id: str, user_email: str, success_url: str, cancel_url: str) -> dict:
        from app.db.engine import SessionLocal
        from app.models.user import UserProfile

        await self._ensure_plan_exists()

        db = SessionLocal()
        try:
            user = db.query(UserProfile).filter(UserProfile.id == user_id).first()
            flow_customer_id = user.stripe_customer_id if user else None

            if not flow_customer_id:
                flow_customer_id = await self._create_flow_customer(
                    user_id, user_email, user.full_name if user else "",
                )
                if user:
                    user.stripe_customer_id = flow_customer_id
                    db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        url_return = self._card_callback_url(user_id, "premium", success_url, cancel_url)
        result = await self._register_card(flow_customer_id, url_return)
        redirect_url = f"{result['url']}?token={result['token']}"
        return {"url": redirect_url}

    # -- Card callback handler ------------------------------------------------

    async def handle_card_callback(
        self, token: str, user_id: str, plan_slug: str, db: DbSession,
    ) -> dict:
        try:
            status = await self._get_register_status(token)
        except Exception:
            logger.exception("Failed to get card registration status")
            return {"status": "failed", "reason": "api_error"}

        if status.get("status") != "1":
            return {"status": "failed", "reason": "card_not_registered"}

        customer_id = status.get("customerId", "")
        if not customer_id:
            return {"status": "failed", "reason": "missing_customer_id"}

        from app.models.user import UserProfile
        db.query(UserProfile).filter(UserProfile.id == user_id).update({
            "stripe_customer_id": customer_id,
        })
        db.commit()

        try:
            sub_data = await self._create_subscription(customer_id)
        except Exception:
            logger.exception("Failed to create subscription in Flow")
            return {"status": "failed", "reason": "subscription_creation_failed"}

        subscription_id = sub_data.get("subscriptionId", "")
        now = datetime.now(timezone.utc)

        from app.models.subscription import SubscriptionPlan, UserSubscription
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
        if not plan:
            logger.warning("Plan %s not found in local DB", plan_slug)
            return {"status": "failed", "reason": "plan_not_found"}

        existing = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == "active",
            UserSubscription.current_period_end > now,
        ).first()

        if existing:
            existing.stripe_subscription_id = subscription_id
            existing.stripe_session_id = customer_id
            db.commit()
            return {"status": "ok", "subscription_id": subscription_id}

        new_sub = UserSubscription(
            id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
            status="active", current_period_start=now,
            current_period_end=now + timedelta(days=7),
            stripe_subscription_id=subscription_id,
            stripe_session_id=customer_id,
        )
        db.add(new_sub)
        db.query(UserProfile).filter(UserProfile.id == user_id).update({
            "subscription_tier": "premium",
        })
        db.commit()

        return {"status": "ok", "subscription_id": subscription_id}

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
            status_data = await self._get("/payment/getStatus", {"token": token})
        except Exception:
            logger.exception("Failed to get payment status from Flow")
            return {"status": "failed", "reason": "api_error"}

        if str(status_data.get("status")) != "2":
            return {"status": "pending", "message": "payment_not_completed"}

        if flow_order and db.query(UserSubscription).filter(
            UserSubscription.stripe_session_id == flow_order,
        ).first():
            return {"status": "already_processed"}

        commerce_order = status_data.get("commerceOrder", "")
        parsed = self._parse_commerce_order(commerce_order)
        if not parsed:
            payment_flow_order = status_data.get("flowOrder", "")
            return {"status": "skipped", "reason": "recurring_payment_not_handled", "flow_order": payment_flow_order}

        user_id, plan_slug = parsed

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == plan_slug).first()
        if not plan:
            return {"status": "skipped", "reason": "invalid_plan"}

        now = datetime.now(timezone.utc)
        days = 30 if plan_slug == "premium" else (7 if plan_slug == "exam_week_pass" else 365)

        new_sub = UserSubscription(
            id=str(uuid4()), user_id=user_id, plan_id=str(plan.id),
            status="active", current_period_start=now,
            current_period_end=now + timedelta(days=days),
            stripe_session_id=flow_order or str(status_data.get("flowOrder", "")),
        )
        db.add(new_sub)
        db.query(UserProfile).filter(UserProfile.id == user_id).update({
            "subscription_tier": "premium",
        })
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
            plan_name=plan.name if plan else "Premium",
            plan_slug=plan.slug if plan else "premium",
            plan_amount=plan.price_cents / 100 if plan else 14.99,
            plan_interval=plan.interval if plan else "month",
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
                    "at_period_end": 1,
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
