"""
LemonSqueezy Payment Provider — Merchant of Record for global tax compliance.
Uses JSON:API format. Replaces Paddle.
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider, SubscriptionInfo
from app.services.email_service import send_trial_welcome_email, send_purchase_confirmation, send_payment_failed_email
from app.models.subscription import UserSubscription

logger = logging.getLogger(__name__)


class LemonSqueezyProvider(PaymentProvider):

    @property
    def provider_name(self) -> str:
        return "lemonsqueezy"

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _base_url() -> str:
        return "https://api.lemonsqueezy.com/v1"

    def _headers(self) -> dict:
        s = get_settings()
        return {
            "Authorization": f"Bearer {s.lemonsqueezy_api_key.strip()}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }

    def _product_id(self, plan_slug: str) -> str:
        s = get_settings()
        mapping = {
            "premium": s.lemonsqueezy_product_premium,
            "exam_week_pass": s.lemonsqueezy_product_exam_week,
        }
        pid = mapping.get(plan_slug)
        if not pid:
            raise ValueError(f"No LemonSqueezy product configured for: {plan_slug}")
        return pid

    async def _fetch_first_variant(self, product_id: str) -> str:
        """Fetch the first variant ID for a given product."""
        cached = getattr(self, "_variant_cache", None)
        if cached and cached.get(product_id):
            return cached[product_id]

        data = await self._get(
            "/variants",
            params={"filter[product_id]": product_id, "page[size]": "1"},
        )
        variants = data.get("data", [])
        if not variants:
            raise ValueError(f"No variants found for product {product_id}")

        vid = self._id(variants[0])
        if not cached:
            self._variant_cache = {}
        self._variant_cache[product_id] = vid
        return vid

    async def _post(self, path: str, body: dict, headers: dict | None = None) -> dict:
        h = headers or self._headers()
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{self._base_url()}{path}", headers=h, json=body)
            if r.status_code >= 400:
                logger.error("LS POST %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self._base_url()}{path}", headers=self._headers(), params=params)
            if r.status_code >= 400:
                logger.error("LS GET %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _patch(self, path: str, body: dict) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.patch(f"{self._base_url()}{path}", headers=self._headers(), json=body)
            if r.status_code >= 400:
                logger.error("LS PATCH %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    async def _delete(self, path: str) -> dict:
        async with httpx.AsyncClient() as client:
            r = await client.delete(f"{self._base_url()}{path}", headers=self._headers())
            if r.status_code >= 400:
                logger.error("LS DELETE %s failed: %s — %s", path, r.status_code, r.text)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _attr(data: dict, key: str | None = None, default=None):
        if not isinstance(data, dict):
            return default
        attrs = data.get("attributes") or {}
        if key is None:
            return attrs
        return attrs.get(key, default)

    @staticmethod
    def _id(data: dict) -> str | None:
        if isinstance(data, dict):
            return str(data.get("id", ""))
        return None

    @staticmethod
    def _map_status(raw: str) -> str:
        """Map LemonSqueezy status values to our internal enum values."""
        if raw == "on_trial":
            return "trialing"
        return raw

    # -- create_checkout ------------------------------------------------------

    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        s = get_settings()
        product_id = self._product_id(plan_slug)
        variant_id = await self._fetch_first_variant(product_id)

        logger.info("Creating LS checkout product=%s variant=%s store=%s plan=%s user=%s",
                    product_id, variant_id, s.lemonsqueezy_store_id, plan_slug, user_id)

        body = {
            "data": {
                "type": "checkouts",
                "attributes": {
                    "product_options": {
                        "redirect_url": success_url,
                    },
                    "checkout_data": {
                        "email": user_email,
                        "custom": {
                            "user_id": user_id,
                            "plan_slug": plan_slug,
                        }
                    },
                },
                "relationships": {
                    "store": {"data": {"type": "stores", "id": str(s.lemonsqueezy_store_id)}},
                    "variant": {"data": {"type": "variants", "id": str(variant_id)}},
                },
            }
        }

        import json as _json
        logger.info("LS checkout body: %s", _json.dumps(body))

        data = await self._post("/checkouts", body)
        checkout_attrs = self._attr(data.get("data", {}))
        return {
            "url": checkout_attrs.get("url", ""),
            "checkout_id": self._id(data.get("data", {})),
        }

    # -- webhook --------------------------------------------------------------

    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """LemonSqueezy webhooks use HMAC-SHA256 hex digest in X-Signature header."""
        s = get_settings()
        secret = getattr(s, "lemonsqueezy_webhook_secret", "") or ""
        computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, signature):
            raise ValueError("Invalid LemonSqueezy webhook signature")

        return json.loads(payload)

    async def process_webhook_event(
        self, event: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        event_name = event.get("meta", {}).get("event_name", "")
        data = event.get("data", {})
        attrs = self._attr(data)
        sub_id = self._id(data)

        logger.info("Webhook received event_type=%s entity_id=%s", event_name, sub_id)

        if event_name == "order_created":
            logger.info("Order created order=%s — provisioning handled by subscription_created", sub_id)
            return {"status": "ok"}

        if event_name == "subscription_created":
            return self._handle_subscription_created(attrs, data, db, UserProfile, UserSubscription, SubscriptionPlan)

        if event_name == "subscription_payment_success":
            return self._handle_subscription_payment_success(attrs, data, db, UserProfile, UserSubscription)

        if event_name in ("subscription_cancelled", "subscription_expired"):
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
                    logger.info("Subscription canceled/expired sub=%s user=%s", sub_id, sub.user_id)
                else:
                    logger.warning("Subscription not found for cancel event sub=%s", sub_id)
            return {"status": "ok"}

        if event_name == "subscription_updated":
            if sub_id:
                sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == sub_id,
                ).first()
                if sub:
                    raw_status = attrs.get("status")
                    new_status = self._map_status(raw_status) if raw_status else None
                    if sub.status == "canceled" and new_status == "active":
                        logger.warning("Skipping canceled→active transition for sub=%s", sub_id)
                        return {"status": "skipped", "reason": "canceled_to_active"}
                    if new_status:
                        sub.status = new_status
                    renews = attrs.get("renews_at")
                    if renews:
                        sub.current_period_end = datetime.fromisoformat(renews.replace("Z", "+00:00"))
                    ends = attrs.get("ends_at")
                    if ends:
                        sub.current_period_end = datetime.fromisoformat(ends.replace("Z", "+00:00"))
                    db.commit()
                    logger.info("Subscription updated sub=%s user=%s status=%s", sub_id, sub.user_id, sub.status)
                else:
                    logger.warning("Subscription not found for update event sub=%s", sub_id)
            return {"status": "ok"}

        if event_name == "subscription_payment_failed":
            return self._handle_subscription_payment_failed(attrs, db, UserProfile)

        return {"status": "unhandled_event", "type": event_name}

    # -- subscription_created handler -----------------------------------------

    def _handle_subscription_created(
        self, attrs: dict, data: dict, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        """LS webhook payload has user_email, status, renews_at — find user by email."""
        sub_id = self._id(data)
        user_email = attrs.get("user_email", "")
        order_id = str(attrs.get("order_id", ""))

        if not sub_id or not user_email:
            return {"status": "skipped", "reason": "missing_subscription_id_or_email"}

        # Idempotency
        if db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == sub_id,
        ).first():
            logger.info("Subscription already exists sub=%s", sub_id)
            return {"status": "already_processed"}

        user = db.query(UserProfile).filter(UserProfile.email == user_email).first()
        if not user:
            logger.warning("User not found for email=%s", user_email)
            return {"status": "skipped", "reason": "user_not_found"}

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == "premium").first()
        if not plan:
            return {"status": "skipped", "reason": "premium_plan_not_found"}

        now = datetime.now(timezone.utc)
        renews_at = attrs.get("renews_at")
        current_period_end = (
            datetime.fromisoformat(renews_at.replace("Z", "+00:00"))
            if renews_at else now + timedelta(days=30)
        )
        raw_status = attrs.get("status", "active")
        status = self._map_status(raw_status)

        new_sub = UserSubscription(
            id=str(uuid4()), user_id=str(user.id), plan_id=str(plan.id),
            status=status, current_period_start=now, current_period_end=current_period_end,
            stripe_subscription_id=sub_id, stripe_session_id=order_id,
        )
        db.add(new_sub)

        update = {"subscription_tier": "premium"}
        if not user.upgraded_at:
            update["upgraded_at"] = now
        db.query(UserProfile).filter(UserProfile.id == str(user.id)).update(update)
        db.flush()

        total_cents = int(attrs.get("total", 0))
        if total_cents > 0:
            from app.models.subscription import UserPayment
            db.add(UserPayment(
                user_id=str(user.id), subscription_id=new_sub.id,
                amount_clp=total_cents, currency=attrs.get("currency", "USD"),
                flow_order=order_id, flow_invoice_id=f"ls_inv_{order_id}",
                period_start=now, period_end=current_period_end,
                payment_type="first_charge",
            ))

        if total_cents == 0 or status == "on_trial":
            send_trial_welcome_email(
                to_email=user.email,
                name=user.full_name or "there",
            )

        logger.info("Subscription created via webhook user=%s sub=%s order=%s amount=%s",
                    user.id, sub_id, order_id, total_cents)
        db.commit()
        return {"status": "ok"}

    # -- subscription_payment_success handler ---------------------------------

    def _handle_subscription_payment_success(
        self, attrs: dict, data: dict, db: DbSession,
        UserProfile, UserSubscription,
    ) -> dict:
        sub_id = str(attrs.get("subscription_id", ""))
        order_id = self._id(data)

        existing_sub = db.query(UserSubscription).filter(
            UserSubscription.stripe_subscription_id == sub_id,
        ).first()

        if not existing_sub:
            logger.warning("Subscription not found for payment success sub=%s", sub_id)
            return {"status": "skipped", "reason": "no_subscription"}

        # Idempotency
        from app.models.subscription import UserPayment
        if db.query(UserPayment).filter(UserPayment.flow_order == order_id).first():
            logger.info("Duplicate payment skipped order=%s", order_id)
            return {"status": "already_processed"}

        now = datetime.now(timezone.utc)
        renews_at = attrs.get("renews_at")
        existing_sub.current_period_end = (
            datetime.fromisoformat(renews_at.replace("Z", "+00:00"))
            if renews_at else now + timedelta(days=30)
        )
        db.flush()

        total_cents = int(attrs.get("total", 0))
        if total_cents > 0:
            prev_payments = db.query(UserPayment).filter(
                UserPayment.subscription_id == existing_sub.id,
            ).count()
            is_first_charge = prev_payments == 0

            db.add(UserPayment(
                user_id=str(existing_sub.user_id), subscription_id=existing_sub.id,
                amount_clp=total_cents, currency=attrs.get("currency", "USD"),
                flow_order=order_id, flow_invoice_id=f"ls_inv_{order_id}",
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
                        amount=f"${total_cents / 100:.2f}/month",
                        period=f"Next billing: {existing_sub.current_period_end.strftime('%B %d, %Y')}",
                    )

        logger.info("Subscription payment renewed sub=%s user=%s amount=%s order=%s",
                    sub_id, existing_sub.user_id, total_cents, order_id)
        db.commit()
        return {"status": "renewed", "subscription_id": sub_id}

    # -- subscription_payment_failed handler -----------------------------------

    def _handle_subscription_payment_failed(
        self, attrs: dict, db: DbSession, UserProfile,
    ) -> dict:
        sub_id = str(attrs.get("subscription_id", ""))
        user_email = attrs.get("user_email", "")

        if not user_email:
            return {"status": "skipped", "reason": "no_email"}

        user = db.query(UserProfile).filter(UserProfile.email == user_email).first()
        if not user:
            logger.warning("Payment failed: user not found email=%s", user_email)
            return {"status": "skipped", "reason": "user_not_found"}

        send_payment_failed_email(
            to_email=user.email,
            name=user.full_name or "there",
        )
        logger.info("Payment failed email sent user=%s sub=%s", user.id, sub_id)
        return {"status": "ok"}

    # -- verify_transaction ---------------------------------------------------

    async def verify_transaction(
        self, checkout_id: str, user_id: str, db: DbSession,
        UserProfile, UserSubscription, SubscriptionPlan,
    ) -> dict:
        """LS checkouts have no status field — webhook subscription_created provisions subscriptions."""
        existing = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "trialing"]),
            UserSubscription.current_period_end > datetime.now(timezone.utc),
        ).first()
        if existing:
            logger.info("LS verify: subscription already active user=%s", user_id)
            return {"status": "already_processed"}

        logger.info("LS verify: no subscription yet user=%s, waiting for webhook", user_id)
        return {"status": "pending", "message": "Subscription is being provisioned. Please wait a moment."}

    # -- get_subscription -----------------------------------------------------

    async def get_subscription(self, user_id: str, db: DbSession, UserSubscription) -> SubscriptionInfo:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing", "cancel_at_period_end"]),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        logger.info("LS get_subscription user=%s found=%s status=%s cancel=%s",
                    user_id, sub is not None, sub.status if sub else "none",
                    not sub.auto_renew if sub else "none")

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
                attrs = self._attr(sub_data)
                card_last4 = attrs.get("card_last_four")
                card_brand = attrs.get("card_brand")
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
            await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                "data": {
                    "type": "subscriptions",
                    "id": sub.stripe_subscription_id,
                    "attributes": {"cancelled": True},
                }
            })

        sub.status = "cancel_at_period_end"
        sub.auto_renew = False
        db.commit()
        logger.info("Subscription canceled user=%s sub=%s", user_id, sub.stripe_subscription_id)
        return {"status": "ok", "canceled_at_period_end": True}

    async def reactivate_subscription(self, user_id: str, db: DbSession, UserSubscription) -> dict:
        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "cancel_at_period_end"]),
        ).first()
        if not sub:
            raise ValueError("No active subscription found")

        if sub.stripe_subscription_id:
            try:
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "data": {
                        "type": "subscriptions",
                        "id": sub.stripe_subscription_id,
                        "attributes": {"cancelled": False},
                    }
                })
            except Exception:
                logger.exception("LS reactivate subscription failed")

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

        if sub.stripe_subscription_id:
            try:
                new_product_id = self._product_id(new_plan_slug)
                new_variant_id = await self._fetch_first_variant(new_product_id)
                await self._patch(f"/subscriptions/{sub.stripe_subscription_id}", {
                    "data": {
                        "type": "subscriptions",
                        "id": sub.stripe_subscription_id,
                        "attributes": {
                            "variant_id": int(new_variant_id),
                            "invoice_immediately": True,
                        },
                    }
                })
            except Exception:
                logger.exception("LS switch plan failed")

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.slug == new_plan_slug).first()
        if plan:
            sub.plan_id = str(plan.id)
        db.commit()

        return {"status": "ok", "plan": new_plan_slug}

    async def create_portal(self, user_id: str, db: DbSession, UserProfile) -> dict:
        s = get_settings()

        sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status.in_(["active", "past_due", "trialing", "cancel_at_period_end"]),
            UserSubscription.stripe_subscription_id.isnot(None),
        ).order_by(UserSubscription.current_period_end.desc()).first()

        if sub and sub.stripe_subscription_id:
            try:
                data = await self._get(f"/subscriptions/{sub.stripe_subscription_id}")
                sub_data = data.get("data", {})
                attrs = self._attr(sub_data)
                urls = attrs.get("urls", {})
                customer_portal = urls.get("customer_portal")
                update_payment = urls.get("update_payment_method")
                logger.info("LS portal urls: customer_portal=%s update_payment=%s sub=%s",
                           bool(customer_portal), bool(update_payment), sub.stripe_subscription_id)
                if customer_portal:
                    return {"url": customer_portal}
                if update_payment:
                    return {"url": update_payment}
            except Exception:
                logger.exception("Failed to get LS subscription urls for portal sub=%s", sub.stripe_subscription_id)
            raise ValueError("Could not open billing portal. Please try again later.")

        raise ValueError("No active subscription found. Please contact support.")

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
