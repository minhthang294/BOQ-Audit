from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import admin_user
from app.auth.security import hash_password
from app.core.database import get_db
from app.models.entities import Job, User, UserRole, UserSession
from app.schemas.api import AdminCreateUser, AdminUpdateUser, AdminUserResponse

router = APIRouter(prefix="/admin/users", tags=["admin-users"], dependencies=[Depends(admin_user)])


def user_response(user: User, db: Session) -> AdminUserResponse:
    count = db.scalar(select(func.count(Job.id)).where(Job.user_id == user.id))
    return AdminUserResponse.model_validate(user).model_copy(update={"job_count": count})


def customer(user_id: int, db: Session) -> User:
    user = db.get(User, user_id)
    if not user or user.role != UserRole.CUSTOMER:
        raise HTTPException(404, "Không tìm thấy tài khoản khách hàng")
    return user


def check_username(username: str, db: Session, user_id: int | None = None):
    existing = db.scalar(select(User).where(func.lower(User.email) == username))
    if existing and existing.id != user_id:
        raise HTTPException(409, "Tên đăng nhập đã tồn tại")


def commit(db: Session):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Không thể lưu tài khoản do dữ liệu đang được sử dụng hoặc bị trùng") from None


def lock_accounts(db: Session):
    # Serialize account changes with job creation and session writes in SQLite.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))


@router.get("", response_model=list[AdminUserResponse])
def list_users(db: Session = Depends(get_db)):
    rows = db.execute(
        select(User, func.count(Job.id))
        .outerjoin(Job, Job.user_id == User.id)
        .where(User.role == UserRole.CUSTOMER)
        .group_by(User.id).order_by(User.created_at.desc(), User.id.desc())
    )
    return [AdminUserResponse.model_validate(user).model_copy(update={"job_count": count}) for user, count in rows]


@router.post("", response_model=AdminUserResponse, status_code=201)
def create_user(payload: AdminCreateUser, db: Session = Depends(get_db)):
    lock_accounts(db)
    check_username(payload.username, db)
    user = User(name=payload.name, email=payload.username, password_hash=hash_password(payload.password.get_secret_value()),
                role=UserRole.CUSTOMER, is_active=payload.is_active)
    db.add(user)
    commit(db)
    return user_response(user, db)


@router.patch("/{user_id}", response_model=AdminUserResponse)
def update_user(user_id: int, payload: AdminUpdateUser, db: Session = Depends(get_db)):
    lock_accounts(db)
    user = customer(user_id, db)
    changes = payload.model_dump(exclude_unset=True)
    if "username" in changes:
        check_username(payload.username, db, user.id)
        user.email = payload.username
    if "name" in changes:
        user.name = payload.name
    if "is_active" in changes:
        user.is_active = payload.is_active
    if "password" in changes:
        user.password_hash = hash_password(payload.password.get_secret_value())
    if "password" in changes or "username" in changes or not user.is_active:
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    commit(db)
    return user_response(user, db)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db)):
    lock_accounts(db)
    user = customer(user_id, db)
    if db.scalar(select(func.count(Job.id)).where(Job.user_id == user.id)):
        raise HTTPException(409, "Tài khoản đã có hồ sơ. Hãy khóa tài khoản để giữ dữ liệu.")
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.delete(user)
    commit(db)
    return Response(status_code=204)
