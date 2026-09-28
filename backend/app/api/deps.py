from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.core.database import get_db
from app.models.entities import User, UserRole, UserSession, utcnow


async def current_user(session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    claims = decode_access_token(session) if session else None
    active_session = db.scalar(
        select(UserSession).where(
            UserSession.session_id == claims.session_id,
            UserSession.user_id == claims.user_id,
            UserSession.expires_at > utcnow(),
        )
    ) if claims else None
    user = db.get(User, claims.user_id) if active_session and claims else None
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bạn không có quyền quản trị")
    return user
