import argparse

from sqlalchemy import func, select

from app.auth.security import hash_password
from app.core.database import Base, SessionLocal, engine
from app.models.entities import User, UserRole


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("--name", required=True)
    create.add_argument("--username", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--role", choices=[role.value for role in UserRole], default="CUSTOMER")
    password = sub.add_parser("set-password")
    password.add_argument("--username", required=True)
    password.add_argument("--password", required=True)
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        username = args.username.strip().lower()
        if len(username) < 3 or any(character.isspace() for character in username):
            raise SystemExit("Username phải có ít nhất 3 ký tự và không chứa khoảng trắng")
        user = db.scalar(select(User).where(func.lower(User.email) == username))
        if args.command == "create-user":
            if user:
                raise SystemExit("Username đã tồn tại")
            db.add(User(name=args.name, email=username, password_hash=hash_password(args.password), role=UserRole(args.role)))
        else:
            if not user:
                raise SystemExit("Không tìm thấy người dùng")
            user.password_hash = hash_password(args.password)
        db.commit()
        print("Thành công")


if __name__ == "__main__":
    main()
