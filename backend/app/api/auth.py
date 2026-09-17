from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.auth.security import create_access_token, verify_password
from app.core.config import get_settings
from app.core.database import get_db
from app.models.entities import User
from app.schemas.api import LoginRequest, MessageResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserResponse)
async def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    username = payload.username.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == username))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tên đăng nhập hoặc mật khẩu không đúng")
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
