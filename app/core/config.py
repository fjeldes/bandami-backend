from pydantic_settings import BaseSettings
from pydantic import model_validator, field_validator
from functools import lru_cache
import json


class Settings(BaseSettings):
    database_url: str = "postgresql://ielts:ielts@db:5432/ielts"

    openai_api_key: str = ""
    gemini_api_key: str = ""

    jwt_secret_key: str = ""

    @model_validator(mode="after")
    def validate_jwt(self):
        if len(self.jwt_secret_key) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters long")
        return self

    google_client_id: str = ""
    google_client_secret: str = ""

    brevo_api_key: str = ""
    brevo_from_email: str = "Bandami <contacto@bandami.com>"

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_free: str = ""
    stripe_price_premium: str = ""
    stripe_price_credit_10: str = ""
    stripe_price_credit_25: str = ""
    stripe_price_exam_week: str = ""

    paddle_api_key: str = ""
    paddle_webhook_secret: str = ""
    paddle_price_premium: str = ""
    paddle_price_exam_week: str = ""
    paddle_environment: str = "sandbox"

    lemonsqueezy_api_key: str = ""
    lemonsqueezy_webhook_secret: str = ""
    lemonsqueezy_store_id: str = ""
    lemonsqueezy_product_premium: str = ""
    lemonsqueezy_product_exam_week: str = ""

    flow_api_key: str = ""
    flow_secret_key: str = ""
    flow_environment: str = "sandbox"

    payment_provider: str = "stripe"

    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    environment: str = "production"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v

    default_free_daily_limit: int = 4
    default_premium_daily_limit: int = 30
    default_free_feedback_delay_hours: int = 24

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
