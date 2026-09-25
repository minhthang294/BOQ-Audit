from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    backend_secret_key: str = "development-only-change-me"
    admin_username: str = Field(default="admin", validation_alias=AliasChoices("ADMIN_USERNAME", "ADMIN_EMAIL"))
    admin_password: str = "change-me-now"
    admin_name: str = "Quản trị viên"
    demo_username: str = Field(default="demo", validation_alias=AliasChoices("DEMO_USERNAME", "DEMO_EMAIL"))
    demo_password: str = ""
    demo_name: str = "Khách hàng Demo"
    database_url: str = "sqlite:////data/database/boq.db"
    data_dir: Path = Path("/data")
    max_upload_mb: int = Field(default=500, ge=1, le=2048)
    frontend_url: str = "http://localhost:3000"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cookie_secure: bool = False
    session_expire_hours: int = Field(default=12, ge=1, le=168)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.app_env.strip().lower() != "production":
            return self

        secret = self.backend_secret_key.strip()
        forbidden_secrets = {
            "change-me",
            "secret",
            "development-secret",
            "development-only-change-me",
            "generate_a_random_secret_minimum_32_chars",
        }
        if len(secret) < 32 or secret.lower() in forbidden_secrets:
            raise ValueError("BACKEND_SECRET_KEY must be a non-default value of at least 32 characters in production")

        password = self.admin_password.strip()
        forbidden_passwords = {
            "admin",
            "admin123",
            "change-me",
            "change-me-now",
            "change-this",
            "password",
            "use_a_strong_password",
            "use_a_strong_password_minimum_12_chars",
        }
        if len(password) < 12 or password.lower() in forbidden_passwords:
            raise ValueError("ADMIN_PASSWORD must be a non-default value of at least 12 characters in production")
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        return self

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
