from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.core.database import get_db
from app.models.entities import CustomerActiveSession, User, UserRole, UserSession, utcnow


def session_user(session: str | None, db: Session) -> User:
    claims = decode_access_token(session) if session else None
    active_session = db.scalar(
        select(UserSession).where(
            UserSession.session_id == claims.session_id,
            UserSession.user_id == claims.user_id,
            UserSession.expires_at > utcnow(),
        )
    ) if claims else None
    user = db.get(User, claims.user_id) if claims else None
    if user and user.role == UserRole.CUSTOMER and user.is_active and claims:
        pointer = db.get(CustomerActiveSession, user.id)
        if pointer and pointer.session_id != claims.session_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tài khoản đã đăng nhập trên trình duyệt khác.", headers={"X-Session-Reason": "replaced"})
        if not pointer:
            active_session = None
    if not active_session or not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Phiên đăng nhập không hợp lệ hoặc đã hết hạn")
    return user


async def current_user(session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    return session_user(session, db)


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bạn không có quyền quản trị")
    return user
