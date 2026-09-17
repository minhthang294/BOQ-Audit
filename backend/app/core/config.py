from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    backend_secret_key: str = "development-only-change-me"
    admin_username: str = Field(default="admin", validation_alias=AliasChoices("ADMIN_USERNAME", "ADMIN_EMAIL"))
    admin_password: str = "change-me-now"
    admin_name: str = "Quản trị viên"
    demo_username: str = Field(default="demo", validation_alias=AliasChoices("DEMO_USERNAME", "DEMO_EMAIL"))
    demo_password: str = "demo-change-me"
    demo_name: str = "Khách hàng Demo"
    database_url: str = "sqlite:////data/database/boq.db"
    data_dir: Path = Path("/data")
    max_upload_mb: int = Field(default=500, ge=1, le=2048)
    frontend_url: str = "http://localhost:3000"
    cookie_secure: bool = False
    session_expire_hours: int = Field(default=12, ge=1, le=168)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
