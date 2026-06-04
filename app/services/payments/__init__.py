# ============================================================
# Payment Provider Factory
# ============================================================

from app.core.config import get_settings
from app.services.payments.base import PaymentProvider
from app.services.payments.stripe_provider import StripeProvider
from app.services.payments.paddle_provider import PaddleProvider

_providers: dict[str, PaymentProvider] = {}


def _get_providers() -> dict[str, PaymentProvider]:
    """Lazy-init providers from config."""
    global _providers
    if _providers:
        return _providers
    _providers = {
        "stripe": StripeProvider(),
        "paddle": PaddleProvider(),
    }
    return _providers


def get_payment_provider() -> PaymentProvider:
    """Return the configured payment provider."""
    settings = get_settings()
    name = getattr(settings, "payment_provider", "stripe") or "stripe"
    providers = _get_providers()
    if name not in providers:
        raise ValueError(f"Unknown payment provider: {name}. Available: {list(providers.keys())}")
    return providers[name]
