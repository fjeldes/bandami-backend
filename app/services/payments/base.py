# ============================================================
# Payment Provider — Abstract Base Class + Price Mapping
# Decoupled from Stripe. Paddle, Lemon Squeezy, etc. implement this.
# ============================================================

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SubscriptionInfo:
    has_subscription: bool
    is_one_time: bool = False
    status: str = "none"
    current_period_start: str | None = None
    current_period_end: str | None = None
    cancel_at_period_end: bool = False
    plan_name: str | None = None
    plan_slug: str | None = None
    plan_amount: float = 0
    plan_interval: str = "month"
    card_last4: str | None = None
    card_brand: str | None = None
    invoices: list[dict] = field(default_factory=list)


class PaymentProvider(ABC):

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def create_checkout(
        self, plan_slug: str, user_id: str, user_email: str,
        success_url: str, cancel_url: str, discount_percent: int = 0,
    ) -> dict:
        """Return { 'url': '...' } for redirect-based checkout."""
        ...

    @abstractmethod
    async def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """Verify webhook signature and return parsed event dict."""
        ...

    @abstractmethod
    async def process_webhook_event(
        self, event: dict, db, user_model, subscription_model, subscription_plan_model,
    ) -> dict:
        """Provision/cancel/renew subscription based on webhook event. Returns status dict."""
        ...

    @abstractmethod
    async def get_subscription(self, user_id: str, db) -> SubscriptionInfo:
        ...

    @abstractmethod
    async def cancel_subscription(self, user_id: str, db) -> dict:
        ...

    @abstractmethod
    async def reactivate_subscription(self, user_id: str, db) -> dict:
        ...

    @abstractmethod
    async def switch_plan(self, new_plan_slug: str, user_id: str, user_email: str, frontend_url: str, db) -> dict:
        """Return { 'status': 'ok' } or { 'status': 'redirect_to_checkout', 'url': '...' }."""
        ...

    @abstractmethod
    async def create_portal(self, user_id: str, db) -> dict:
        """Return { 'url': '...' } for billing portal."""
        ...

    @abstractmethod
    async def get_invoices(self, user_id: str, db) -> list[dict]:
        ...
