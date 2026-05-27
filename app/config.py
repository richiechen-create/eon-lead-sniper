from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Multiple dotenv files supported, last wins. `.env.dev` lets the local
    # dev sandbox override prod creds without editing `.env`. In prod (Render)
    # neither file exists — env vars come from the Render env group.
    model_config = SettingsConfigDict(env_file=(".env", ".env.dev"), extra="ignore")

    APOLLO_API_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./lead_engine.sqlite"
    FROM_EMAIL: str = "leadengine@leadengine.eonreality.com"
    FROM_NAME: str = "EON Bullseye"

    # SMTP (Gmail via App Password). FROM_EMAIL should match SMTP_USERNAME.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    ADMIN_EMAIL: str = "admin@eonreality.com"
    DEFAULT_REP_EMAIL: str = "dan@eonreality.com"
    DEFAULT_REP_NAME: str = "Dan (Fallback)"
    APP_ENV: Literal["dev", "prod"] = "dev"
    CREDIT_BUDGET_MONTHLY: int = 9500
    APOLLO_BASE_URL: str = "https://api.apollo.io/api/v1"
    APOLLO_MAX_PAGES: int = 10
    APOLLO_PER_PAGE: int = 25

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change-me"
    SESSION_SECRET: str = "dev-only-replace-me-32-bytes-min"
    INTERNAL_API_KEY: str = "dev-internal-key"


@lru_cache
def get_settings() -> Settings:
    return Settings()
