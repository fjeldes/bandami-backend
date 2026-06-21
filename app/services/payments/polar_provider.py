"""
Polar.sh Payment Provider — Open-source Merchant of Record.
https://docs.polar.sh/api
"""
import base64
import json
import logging
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import httpx
from polar_sdk.webhooks import validate_event
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider, SubscriptionInfo
from app.services.email_service import send_trial_welcome_email, send_purchase_confirmation, send_payment_failed_email
from app.models.subscription import UserSubscription

logger = logging.getLogger(__name__)


class PolarProvider(PaymentProvider):

    @property
    def provider_name(self) -> str:
        return "polar"

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _base_url() -> str:
        s = get_settings()
        env = getattr(s, "polar_environment", "sandbox") or "sandbox"
        if env == "production":
            return "https://api.polar.sh/v1"
        return "https://sandbox-api.polar.sh/v1"

    def _headers(self) -> dict:
        s = get_settings()
        return {
            "Authorization": f"Bearer {s.polar_access_token.strip()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _post(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{self._base_url()}{path}", headers=self._headers(), json=body)
            if r.status_code >= 400:
                logger.error("Polar POST %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self._base_url()}{path}", headers=self._headers(), params=params)
            if r.status_code >= 400:
                logger.error("Polar GET %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _patch(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.patch(f"{self._base_url()}{path}", headers=self._headers(), json=body)
            if r.status_code >= 400:
                logger.error("Polar PATCH %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    def _product_id(self, plan_slug: str) -> str:
        s = get_settings()
        if plan_slug == "premium":
            pid = s.polar_product_premium
        else:
            pid = ""
        if not pid:
            raise ValueError(f"No Polar.sh product configured for: {plan_slug}")
        return pid

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        product_id = self._product_id(plan_slug)

        logger.info("Creating Polar checkout product=%s plan=%s user=%s",
                    product_id, plan_slug, user_id)

        body = {
            "product_id": product_id,
            "success_url": success_url,
            "customer_email": user_email,
            "metadata": {
                "user_id": user_id,
                "plan_slug": plan_slug,
            },
        }

        data = await self._post("/checkouts/", body)
        return {
            "url": data.get("url", ""),
            "checkout_id": data.get("id", ""),
        }

    # -- webhook --------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        try:
            headers = json.loads(signature)
        except (json.JSONDecodeError, TypeError):
            raise ValueError("Invalid webhook headers format")

        s = get_settings()
        secret = getattr(s, "polar_webhook_secret", "") or ""
        if not secret:
            raise ValueError("Missing Polar.sh webhook secret")

        event = validate_event(
            body=payload,
            headers=headers,
            secret=secret,
        )
        return json.loads(event.model_dump_json())

    async def process_webhook_event(
        self, event: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        event_type = event.get("type", "")
        data = event.get("data", {})
        sub_id = data.get("id", "")

        logger.info("Polar webhook type=%s id=%s", event_type, sub_id)

        if event_type == "checkout.updated":
            checkout_data = data.get("data", event)
            status = checkout_data.get("status", "")
            logger.info("Polar checkout updated id=%s status=%s", sub_id, status)
            return {"status": "ok"}

        if event_type == "subscription.created":
            return self._handle_subscription_created(data, db, UserProfile, UserSubscription, SubscriptionPlan)

        if event_type in ("subscription.active", "subscription.updated"):
            return self._handle_subscription_updated(data, db, UserProfile, UserSubscription)

        if event_type == "subscription.canceled":
            return self._handle_subscription_canceled(data, db, UserProfile, UserSubscription)

        if event_type == "order.created":
            order_data = data.get("data", event)
            return self._handle_order_created(order_data, db, UserProfile, UserSubscription)

        return {"status": "unhandled_event", "type": event_type}

    # -- subscription_created handler -----------------------------------------

    def _handle_subscription_created(
        self, data: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        sub_id = data.get("id", "")
        customer = data.get("customer", {})
        user_email = customer.get("email", "")

        if not sub_id or not user_email:
            return {"status": "skipped", "reason": "missing_id_or_email"}

        if db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == sub_id,
        ).first():
            logger.info("Subscription already exists sub=%s", sub_id)
            return {"status": "already_processed"}

        user = db.query(UserProfile).filter(UserProfile.email == user_email).first()
        if not user:
            logger.warning("User not found email=%s", user_email)
            return {"status": "skipped", "reason": "user_not_found"}

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == "premium").first()
        if not plan:
            return {"status": "skipped", "reason": "premium_plan_not_found"}

        now = datetime.now(timezone.utc)
        raw_status = data.get("status", "active")
        status = raw_status if raw_status != "trialing" else "trialing"

        current_period_end = now + timedelta(days=30)
        current_period_end_raw = data.get("current_period_end")
        if current_period_end_raw:
            try:
                current_period_end = datetime.fromisoformat(current_period_end_raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        amount_raw = data.get("amount")
        total_cents = 0
        if amount_raw:
            total_cents = int(amount_raw)

        new_sub = UserSubscription(
            id=str(uuid4()), user_id=str(user.id), plan_id=str(plan.id),
            status=status, current_period_start=now, current_period_end=current_period_end,
            stripe_subscription_id=sub_id, stripe_session_id=sub_id,
        )
        db.add(new_sub)

        update = {"subscription_tier": "premium"}
        if not user.upgraded_at:
            update["upgraded_at"] = now
        db.query(UserProfile).filter(UserProfile.id == str(user.id)).update(update)
        db.flush()

        if total_cents > 0:
            from app.models.subscription import UserPayment
            db.add(UserPayment(
                user_id=str(user.id), subscription_id=new_sub.id,
                amount_clp=total_cents, currency="USD",
                flow_order=sub_id, flow_invoice_id=f"polar_{sub_id}",
                period_start=now, period_end=current_period_end,
                payment_type="first_charge",
            ))

        if total_cents == 0 or status == "trialing":
            send_trial_welcome_email(
                to_email=user.email,
                name=user.full_name or "there",
            )

        logger.info("Polar subscription created user=%s sub=%s amount=%s",
                    user.id, sub_id, total_cents)
        db.commit()
        return {"status": "ok"}

    # -- subscription_updated handler -----------------------------------------

    def _handle_subscription_updated(
        self, data: dict, db: DbSession,
        UserProfile, UserSubscription,
    ) -> dict:
        sub_id = data.get("id", "")
        if not sub_id:
            return {"status": "skipped"}

        sub = db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == sub_id,
        ).first()
        if not sub:
            logger.warning("Polar subscription not found for update sub=%s", sub_id)
            return {"status": "skipped"}

        raw_status = data.get("status")
        if raw_status and sub.status != "cancel_at_period_end":
            sub.status = raw_status

        period_end = data.get("current_period_end")
        if period_end:
            try:
                sub.current_period_end = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        db.commit()
        logger.info("Polar subscription updated sub=%s status=%s", sub_id, sub.status)
        return {"status": "ok"}

    # -- subscription_canceled handler ----------------------------------------

    def _handle_subscription_canceled(
        self, data: dict, db: DbSession,
        UserProfile, UserSubscription,
    ) -> dict:
        sub_id = data.get("id", "")
        if not sub_id:
            return {"status": "skipped"}

        sub = db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == sub_id,
        ).first()
        if not sub:
            logger.warning("Polar subscription not found for cancel sub=%s", sub_id)
            return {"status": "skipped"}

        if sub.status == "cancel_at_period_end":
            logger.info("Skipping canceled webhook — already cancel_at_period_end sub=%s", sub_id)
            return {"status": "skipped"}

        sub.status = "canceled"
        sub.canceled_at = datetime.now(timezone.utc)
        sub.auto_renew = False
        db.query(UserProfile).filter(UserProfile.id == sub.user_id).update({"subscription_tier": "free"})
        db.commit()
        logger.info("Polar subscription canceled sub=%s user=%s", sub_id, sub.user_id)
        return {"status": "ok"}

    # -- order_created handler ------------------------------------------------

    def _handle_order_created(
        self, data: dict, db: DbSession,
        UserProfile, UserSubscription,
    ) -> dict:
        sub_id = data.get("subscription_id", "")
        order_id = data.get("id", "")
        amount_raw = data.get("amount")

        if not sub_id or not order_id:
            return {"status": "skipped"}

        from app.models.subscription import UserPayment
        if db.query(UserPayment).filter(UserPayment.flow_order == order_id).first():
            logger.info("Duplicate Polar order skipped order=%s", order_id)
            return {"status": "already_processed"}

        sub = db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == sub_id,
        ).first()
        if not sub:
            return {"status": "skipped", "reason": "no_subscription"}

        now = datetime.now(timezone.utc)
        period_end = data.get("current_period_end")
        if period_end:
            try:
                sub.current_period_end = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        total_cents = int(amount_raw) if amount_raw else 0
        if total_cents > 0:
            prev_payments = db.query(UserPayment).filter(
                UserPayment.subscription_id == sub.id,
            ).count()
            is_first = prev_payments == 0

            db.add(UserPayment(
                user_id=str(sub.user_id), subscription_id=sub.id,
                amount_clp=total_cents, currency="USD",
                flow_order=order_id, flow_invoice_id=f"polar_{order_id}",
                period_start=now, period_end=sub.current_period_end,
                payment_type="recurring",
            ))

            if is_first:
                user = db.query(UserProfile).filter(UserProfile.id == sub.user_id).first()
                if user and user.email:
                    send_purchase_confirmation(
                        to_email=user.email,
                        name=user.full_name or "there",
                        plan_name="Pro Monthly",
                        amount=f"${total_cents / 100:.2f}/month",
                        period=f"Next billing: {sub.current_period_end.strftime('%B %d, %Y')}" if sub.current_period_end else "",
                    )

        db.commit()
        logger.info("Polar order created sub=%s order=%s amount=%s", sub_id, order_id, total_cents)
        return {"status": "ok"}

    # -- verify_transaction ---------------------------------------------------

    async def verify_transaction(
        self, checkout_id: str, user_id: str, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "trialing"]),
            UserSubscription.current_period_end > datetime.now(timezone.utc),
        ).first()
        if sub:
            logger.info("Polar verify: subscription active user=%s", user_id)
            return {"status": "ok", "subscription_id": sub.stripe_subscription_id}
        logger.info("Polar verify: no subscription yet user=%s, waiting for webhook", user_id)
        return {"status": "pending", "message": "Subscription is being provisioned. Please wait a moment."}

    # -- get_subscription -----------------------------------------------------

    async def get_subscription(self, user_id: str, db: DbSession, UserSubscription) -> SubscriptionInfo:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing", "cancel_at_period_end", "canceled"]),
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
                card_last4 = data.get("card", {}).get("last4") if isinstance(data.get("card"), dict) else None
                card_brand = data.get("card", {}).get("brand") if isinstance(data.get("card"), dict) else None
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

    # -- cancel_subscription --------------------------------------------------

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
                    "cancel_at_period_end": True,
                })
            except Exception:
                logger.exception("Polar cancel PATCH failed sub=%s", sub.stripe_subscription_id)

        sub.status = "cancel_at_period_end"
        sub.auto_renew = False
        db.commit()
        logger.info("Polar subscription cancel_at_period_end user=%s sub=%s", user_id, sub.stripe_subscription_id)
        return {"status": "ok", "canceled_at_period_end": True}

    # -- reactivate_subscription ----------------------------------------------

    async def reactivate_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "cancel_at_period_end", "canceled"]),
        ).first()
        if not sub:
            raise ValueError("No subscription found to reactivate")

        if sub.stripe_subscription_id:
            try:
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "cancel_at_period_end": False,
                })
            except Exception:
                logger.exception("Polar reactivate failed sub=%s", sub.stripe_subscription_id)

        sub.auto_renew = True
        db.commit()
        return {"status": "ok"}

    # -- switch_plan ----------------------------------------------------------

    async def switch_plan(
        self, new_plan_slug: str, user_id: str, user_email: str,
        frontend_url: str, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        s = get_settings()

        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing", "cancel_at_period_end"]),
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

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == new_plan_slug).first()
        if plan:
            sub.plan_id = str(plan.id)
        db.commit()

        return {"status": "ok", "plan": new_plan_slug}

    # -- create_portal --------------------------------------------------------

    async def create_portal(self, user_id: str, db: DbSession, UserProfile) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing", "cancel_at_period_end"]),
            UserSubscription.stripe_subscription_id.isnot(None),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if sub and sub.stripe_subscription_id:
            try:
                data = await self._get("/customer-portal/subscriptions/")
                items = data.get("items", [])
                for item in items:
                    if item.get("id") == sub.stripe_subscription_id:
                        portal_url = item.get("customer_portal_url")
                        if portal_url:
                            return {"url": portal_url}
            except Exception:
                logger.exception("Failed to get Polar customer portal sub=%s", sub.stripe_subscription_id)

        raise ValueError("Could not open billing portal. Please contact support.")

    # -- get_invoices ---------------------------------------------------------

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
            "lemon_order_id": p.flow_order,
        } for p in payments]
