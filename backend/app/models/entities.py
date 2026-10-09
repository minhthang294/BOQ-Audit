import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    ADMIN = "ADMIN"


class JobStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    PROCESSING = "PROCESSING"
    WAITING_FOR_INFO = "WAITING_FOR_INFO"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OutputType(str, enum.Enum):
    EXCEL_REPORT = "EXCEL_REPORT"
    ESTIMATE_REPORT = "ESTIMATE_REPORT"
    ANNOTATED_PDF = "ANNOTATED_PDF"
    OTHER = "OTHER"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.CUSTOMER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")

    @property
    def username(self) -> str:
        # Giữ tên cột legacy để database V1 hiện hữu không cần migration phá vỡ.
        return self.email


class UserSession(Base):
    __tablename__ = "user_sessions"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomerActiveSession(Base):
    __tablename__ = "customer_active_sessions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("user_sessions.session_id", ondelete="CASCADE"), unique=True)


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"
    __table_args__ = (Index("ix_rate_limit_scope_key_time", "scope", "key_hash", "occurred_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(32))
    key_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_code: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    input_file_path: Mapped[str] = mapped_column(String(600))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.SUBMITTED, index=True)
    critical_errors: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[int] = mapped_column(Integer, default=0)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    user: Mapped[User] = relationship(back_populates="jobs")
    outputs: Mapped[list["JobOutput"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    audit_runs: Mapped[list["AuditRun"]] = relationship(back_populates="job", cascade="all, delete-orphan", order_by="AuditRun.attempt", lazy="selectin")
    narrative_input: Mapped[Optional["JobNarrativeInput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    estimate_input: Mapped[Optional["JobEstimateInput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )

    @property
    def turnaround_seconds(self) -> float:
        end = self.completed_at if self.status in (JobStatus.COMPLETED, JobStatus.FAILED) else None
        return max(0, ((end or utcnow()).replace(tzinfo=timezone.utc) - self.created_at.replace(tzinfo=timezone.utc)).total_seconds())

    @property
    def ai_processing_seconds(self) -> float | None:
        if not self.audit_runs:
            return None
        return sum(run.duration_seconds or 0 for run in self.audit_runs)

    @property
    def ai_timing_complete(self) -> bool:
        return bool(self.audit_runs) and all(run.status == "RUNNING" or run.duration_seconds is not None for run in self.audit_runs)

    @property
    def current_attempt_seconds(self) -> float | None:
        running = next((run for run in self.audit_runs if run.status == "RUNNING"), None)
        return max(0, (utcnow() - running.started_at.replace(tzinfo=timezone.utc)).total_seconds()) if running else None


class AuditRun(Base):
    __tablename__ = "audit_runs"
    __table_args__ = (UniqueConstraint("job_id", "attempt"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    job: Mapped[Job] = relationship(back_populates="audit_runs")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    context_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    messages: Mapped[list["ChatMessage"]] = relationship(cascade="all, delete-orphan", order_by="ChatMessage.id")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("chat_conversations.job_id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="COMPLETED")
    context_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobEstimateInput(Base):
    __tablename__ = "job_estimate_inputs"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(600))
    file_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[Job] = relationship(back_populates="estimate_input")


class JobNarrativeInput(Base):
    __tablename__ = "job_narrative_inputs"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(600))
    file_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[Job] = relationship(back_populates="narrative_input")


class JobOutput(Base):
    __tablename__ = "job_outputs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    file_type: Mapped[OutputType] = mapped_column(Enum(OutputType))
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(600))
    file_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[Job] = relationship(back_populates="outputs")
