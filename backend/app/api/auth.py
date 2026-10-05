from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert

from app.api.deps import current_user
from app.auth.security import create_access_token, decode_access_token, verify_password
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import PersistentRateLimiter
from app.models.entities import CustomerActiveSession, User, UserRole, UserSession, utcnow
from app.schemas.api import LoginRequest, MessageResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
login_limiter = PersistentRateLimiter(
    "login",
    settings.login_rate_limit,
    settings.login_rate_window_seconds,
)
account_login_limiter = PersistentRateLimiter("login-account", settings.login_rate_limit, settings.login_rate_window_seconds)


@router.post("/login", response_model=UserResponse)
async def login(payload: LoginRequest, response: Response, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not login_limiter.consume(db, client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Quá nhiều lần đăng nhập. Vui lòng thử lại sau.",
            headers={"Retry-After": str(login_limiter.window_seconds)},
        )
    username = payload.username.strip().lower()
    if not account_login_limiter.consume(db, username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Quá nhiều lần đăng nhập. Vui lòng thử lại sau.", headers={"Retry-After": str(account_login_limiter.window_seconds)})
    user = db.scalar(select(User).where(func.lower(User.email) == username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tên đăng nhập hoặc mật khẩu không đúng")

    login_limiter.clear(db, client_ip)
    account_login_limiter.clear(db, username)
    db.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))
    token, claims = create_access_token(user.id)
    db.add(UserSession(session_id=claims.session_id, user_id=user.id, expires_at=claims.expires_at))
    db.flush()
    if user.role == UserRole.CUSTOMER:
        # The user primary key serializes concurrent logins at the database boundary.
        statement = insert(CustomerActiveSession).values(user_id=user.id, session_id=claims.session_id)
        db.execute(statement.on_conflict_do_update(index_elements=[CustomerActiveSession.user_id], set_={"session_id": claims.session_id}))
        db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.session_id != claims.session_id))
    db.commit()
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_expire_hours * 3600,
        path="/",
    )
    return user


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response,
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    claims = decode_access_token(session) if session else None
    if claims:
        db.execute(
            delete(UserSession).where(
                UserSession.session_id == claims.session_id,
                UserSession.user_id == claims.user_id,
            )
        )
        db.commit()
    response.delete_cookie("session", path="/")
    return {"message": "Đã đăng xuất"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(current_user)):
    return user
