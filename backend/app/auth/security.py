from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

ph = PasswordHasher()


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: int
    session_id: str
    expires_at: datetime


def create_access_token(user_id: int) -> tuple[str, AccessTokenClaims]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    claims = AccessTokenClaims(
        user_id=user_id,
        session_id=uuid4().hex,
        expires_at=now + timedelta(hours=settings.session_expire_hours),
    )
    payload = {"sub": str(user_id), "jti": claims.session_id, "iat": now, "exp": claims.expires_at}
    return jwt.encode(payload, settings.backend_secret_key, algorithm="HS256"), claims


def decode_access_token(token: str) -> AccessTokenClaims | None:
    try:
        payload = jwt.decode(token, get_settings().backend_secret_key, algorithms=["HS256"])
        return AccessTokenClaims(
            user_id=int(payload["sub"]),
            session_id=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(float(payload["exp"]), timezone.utc),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None

