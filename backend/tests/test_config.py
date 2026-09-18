import pytest
from pydantic import ValidationError

from app.core.config import Settings


STRONG_SECRET = "a-strong-production-secret-with-32-plus-characters"
STRONG_PASSWORD = "strong-admin-password"


def production_settings(**overrides):
    values = {
        "app_env": "production",
        "backend_secret_key": STRONG_SECRET,
        "admin_password": STRONG_PASSWORD,
        "cookie_secure": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "secret",
    ["", "change-me", "secret", "development-secret", "too-short", "GENERATE_A_RANDOM_SECRET_MINIMUM_32_CHARS"],
)
def test_production_rejects_weak_secret(secret):
    with pytest.raises(ValidationError, match="BACKEND_SECRET_KEY"):
        production_settings(backend_secret_key=secret)


def test_production_rejects_insecure_cookie():
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        production_settings(cookie_secure=False)


@pytest.mark.parametrize(
    "password",
    ["", "password", "admin123", "short-pass", "USE_A_STRONG_PASSWORD_MINIMUM_12_CHARS"],
)
def test_production_rejects_weak_admin_password(password):
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        production_settings(admin_password=password)


def test_production_accepts_strong_configuration():
    assert production_settings().app_env == "production"


def test_demo_account_is_opt_in_by_default():
    assert Settings(_env_file=None, demo_password="").demo_password == ""
