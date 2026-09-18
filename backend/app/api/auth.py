import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.auth.security import create_access_token, verify_password
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import User
from app.schemas.api import LoginRequest, MessageResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


class FailedLoginLimiter:
    def __init__(self, limit: int = 5, window_seconds: int = 60, max_clients: int = 10_000):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        stale_clients = []
        for client, attempts in self._attempts.items():
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                stale_clients.append(client)
        for client in stale_clients:
            self._attempts.pop(client, None)

    def is_limited(self, client: str) -> bool:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            return len(self._attempts.get(client, ())) >= self.limit

    def record_failure(self, client: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if client not in self._attempts and len(self._attempts) >= self.max_clients:
                oldest = min(self._attempts, key=lambda key: self._attempts[key][-1])
                self._attempts.pop(oldest, None)
            self._attempts.setdefault(client, deque()).append(now)

    def clear(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


login_limiter = FailedLoginLimiter()


@router.post("/login", response_model=UserResponse)
async def login(payload: LoginRequest, response: Response, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if login_limiter.is_limited(client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau.",
            headers={"Retry-After": str(login_limiter.window_seconds)},
        )
    username = payload.username.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        login_limiter.record_failure(client_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tên đăng nhập hoặc mật khẩu không đúng")
    login_limiter.clear(client_ip)
    settings = get_settings()
    response.set_cookie(
        "session",
        create_access_token(user.id),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=settings.session_expire_hours * 3600,
        path="/",
    )
    return user


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response):
    response.delete_cookie("session", path="/")
    return {"message": "Đã đăng xuất"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(current_user)):
    return user
