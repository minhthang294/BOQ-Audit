import hashlib
import hmac
from datetime import timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import RateLimitEvent, utcnow


def _key_hash(scope: str, key: str) -> str:
    secret = get_settings().backend_secret_key.encode()
    return hmac.new(secret, f"{scope}:{key}".encode(), hashlib.sha256).hexdigest()


class PersistentRateLimiter:
    def __init__(self, scope: str, limit: int, window_seconds: int):
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds

    def consume(self, db: Session, key: str) -> bool:
        """Atomically record an attempt. Return False when the window is full."""
        cutoff = utcnow() - timedelta(seconds=self.window_seconds)
        key_hash = _key_hash(self.scope, key)
        try:
            if db.bind and db.bind.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            db.execute(
                delete(RateLimitEvent).where(
                    RateLimitEvent.scope == self.scope,
                    RateLimitEvent.occurred_at <= cutoff,
                )
            )
            count = db.scalar(
                select(func.count(RateLimitEvent.id)).where(
                    RateLimitEvent.scope == self.scope,
                    RateLimitEvent.key_hash == key_hash,
                    RateLimitEvent.occurred_at > cutoff,
                )
            ) or 0
            if count >= self.limit:
                db.commit()
                return False
            db.add(RateLimitEvent(scope=self.scope, key_hash=key_hash))
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise

    def clear(self, db: Session, key: str) -> None:
        db.execute(
            delete(RateLimitEvent).where(
                RateLimitEvent.scope == self.scope,
                RateLimitEvent.key_hash == _key_hash(self.scope, key),
            )
        )
        db.commit()

    def reset(self) -> None:
        """Test helper; production state remains database-backed."""
        from app.core.database import SessionLocal

        try:
            with SessionLocal() as db:
                db.execute(delete(RateLimitEvent).where(RateLimitEvent.scope == self.scope))
                db.commit()
        except OperationalError:
            pass
