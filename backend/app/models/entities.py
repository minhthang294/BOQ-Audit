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


class ExcelWorkbook(Base):
    __tablename__ = "excel_workbooks"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(600))
    file_size: Mapped[int] = mapped_column(Integer)
    sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ExcelConversation(Base):
    __tablename__ = "excel_conversations"
    workbook_id: Mapped[int] = mapped_column(ForeignKey("excel_workbooks.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


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


class AuditQualityReview(Base):
    __tablename__ = "audit_quality_reviews"
    __table_args__ = (
        UniqueConstraint("run_id", "snapshot_id", name="uq_audit_quality_reviews_run_snapshot"),
        UniqueConstraint("snapshot_id", name="uq_audit_quality_reviews_snapshot_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("audit_runs.id", name="fk_audit_quality_reviews_run_id"),
        index=True,
    )
    reviewer_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_audit_quality_reviews_reviewer_user_id"),
        nullable=True,
    )
    snapshot_id: Mapped[str] = mapped_column(String(128))
    stage: Mapped[str] = mapped_column(String(16))
    controller_status: Mapped[str] = mapped_column(String(16))
    candidate_release_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_aggregate: Mapped[str] = mapped_column(Text)
    source_digest: Mapped[str] = mapped_column(String(64))
    skill_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str] = mapped_column(String(64))
    receipt_digests: Mapped[str] = mapped_column(Text)
    output_manifest_digest: Mapped[str] = mapped_column(String(64))
    customer_fields_digest: Mapped[str] = mapped_column(String(64))
    output_manifest: Mapped[str] = mapped_column(Text)


class AuditQualityReviewEvent(Base):
    __tablename__ = "audit_quality_review_events"
    event_id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(
        ForeignKey("audit_quality_reviews.id", name="fk_audit_quality_review_events_review_id"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditInventoryApproval(Base):
    __tablename__ = "audit_inventory_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source_digest: Mapped[str] = mapped_column(String(64), index=True)
    inventory_json: Mapped[str] = mapped_column(Text)


class AuditRunCheckpoint(Base):
    __tablename__ = "audit_run_checkpoints"
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), primary_key=True)
    contract_version: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    approval_id: Mapped[int] = mapped_column(ForeignKey("audit_inventory_approvals.id", ondelete="CASCADE"), index=True)
    source_digest: Mapped[str] = mapped_column(String(64))
    skill_digest: Mapped[str] = mapped_column(String(64))
    inventory_digest: Mapped[str] = mapped_column(String(64))
    batch_plan_digest: Mapped[str] = mapped_column(String(64))
    inventory_json: Mapped[str] = mapped_column(Text)
    batch_plan_json: Mapped[str] = mapped_column(Text)
    ledger_json: Mapped[str] = mapped_column(Text)
    lesson_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson_snapshot_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checker_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    derived_from_run_id: Mapped[int | None] = mapped_column(ForeignKey("audit_runs.id"), nullable=True)
    gate_reports_json: Mapped[str] = mapped_column(Text, default="{}")
    pass_no: Mapped[int] = mapped_column(Integer, default=1)
    active_batch_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default="READY")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditRunStageGate(Base):
    __tablename__ = "audit_run_stage_gates"
    __table_args__ = (UniqueConstraint("run_id", "snapshot_id", "stage", name="uq_audit_stage_gate_snapshot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    pass_no: Mapped[int] = mapped_column(Integer)
    checkpoint_version: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(16))
    receipt_json: Mapped[str] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    receipt_digest: Mapped[str] = mapped_column(String(64))
    advanced: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRunBatchResult(Base):
    __tablename__ = "audit_run_batch_results"
    __table_args__ = (UniqueConstraint("run_id", "batch_key", name="uq_audit_run_batch_results_run_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    pass_no: Mapped[int] = mapped_column(Integer)
    batch_key: Mapped[str] = mapped_column(String(64))
    check_ids_json: Mapped[str] = mapped_column(Text)
    source_files_json: Mapped[str] = mapped_column(Text)
    patch_json: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text)
    patch_digest: Mapped[str] = mapped_column(String(64))
    evidence_digest: Mapped[str] = mapped_column(String(64))
    checkpoint_version: Mapped[int] = mapped_column(Integer)
    derived_from_run_id: Mapped[int | None] = mapped_column(ForeignKey("audit_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRunBatchLedger(Base):
    __tablename__ = "audit_run_batch_ledgers"
    __table_args__ = (UniqueConstraint("batch_result_id", name="uq_audit_batch_ledger_batch"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    batch_result_id: Mapped[int] = mapped_column(ForeignKey("audit_run_batch_results.id", ondelete="CASCADE"))
    records_json: Mapped[str] = mapped_column(Text)
    records_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRunBatchRevision(Base):
    __tablename__ = "audit_run_batch_revisions"
    __table_args__ = (UniqueConstraint("base_result_id", "revision_no", name="uq_audit_batch_revision_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    base_result_id: Mapped[int] = mapped_column(ForeignKey("audit_run_batch_results.id", ondelete="CASCADE"), index=True)
    gate_id: Mapped[int] = mapped_column(ForeignKey("audit_run_stage_gates.id"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    blocker_fingerprint: Mapped[str] = mapped_column(String(64))
    patch_json: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text)
    records_json: Mapped[str] = mapped_column(Text)
    patch_digest: Mapped[str] = mapped_column(String(64))
    evidence_digest: Mapped[str] = mapped_column(String(64))
    records_digest: Mapped[str] = mapped_column(String(64))
    invalidated_check_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    invalidation_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    checkpoint_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRunGateRepairRequest(Base):
    __tablename__ = "audit_run_gate_repair_requests"
    __table_args__ = (UniqueConstraint("base_result_id", "attempt_no", name="uq_audit_gate_repair_attempt"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    base_result_id: Mapped[int] = mapped_column(ForeignKey("audit_run_batch_results.id", ondelete="CASCADE"), index=True)
    gate_id: Mapped[int] = mapped_column(ForeignKey("audit_run_stage_gates.id"), index=True)
    batch_ids_json: Mapped[str] = mapped_column(Text)
    source_digest: Mapped[str] = mapped_column(String(64))
    skill_digest: Mapped[str] = mapped_column(String(64))
    checkpoint_version: Mapped[int] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="REQUESTED")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditIssueClassification(Base):
    __tablename__ = "audit_issue_classifications"
    __table_args__ = (UniqueConstraint("run_id", "snapshot_digest", "issue_id", "revision_no", name="uq_audit_issue_classification_revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    issue_id: Mapped[str] = mapped_column(String(160))
    revision_no: Mapped[int] = mapped_column(Integer)
    final_status: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditAnnotationDraft(Base):
    __tablename__ = "audit_annotation_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    classification_digest: Mapped[str] = mapped_column(String(64))
    source_pdf_sha256: Mapped[str] = mapped_column(String(64))
    output_pdf_sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(600))
    marks_json: Mapped[str] = mapped_column(Text)
    qa_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditAnnotationReviewEvent(Base):
    __tablename__ = "audit_annotation_review_events"
    __table_args__ = (UniqueConstraint("draft_id", "mark_id", "revision_no", name="uq_audit_annotation_mark_review_revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("audit_annotation_drafts.id", ondelete="CASCADE"), index=True)
    mark_id: Mapped[str] = mapped_column(String(160))
    revision_no: Mapped[int] = mapped_column(Integer)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text)
    output_pdf_sha256: Mapped[str] = mapped_column(String(64))
    target_crop_sha256: Mapped[str] = mapped_column(String(64))
    note_crop_sha256: Mapped[str] = mapped_column(String(64))
    page_render_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditExcelReportDraft(Base):
    __tablename__ = "audit_excel_report_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    classification_digest: Mapped[str] = mapped_column(String(64))
    annotation_pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workbook_sha256: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[str] = mapped_column(String(600))
    qa_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditExcelReportReviewEvent(Base):
    __tablename__ = "audit_excel_report_review_events"
    __table_args__ = (UniqueConstraint("draft_id", "revision_no", name="uq_audit_excel_report_review_revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("audit_excel_report_drafts.id", ondelete="CASCADE"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text)
    reviewed_sections_json: Mapped[str] = mapped_column(Text)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    classification_digest: Mapped[str] = mapped_column(String(64))
    annotation_pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workbook_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditReleaseSignoffEvent(Base):
    __tablename__ = "audit_release_signoff_events"
    __table_args__ = (UniqueConstraint("run_id", "revision_no", name="uq_audit_release_signoff_revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    result: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    accepted_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_unmarked_pdf: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    classification_digest: Mapped[str] = mapped_column(String(64))
    checkpoint_version: Mapped[int] = mapped_column(Integer)
    approval_id: Mapped[int] = mapped_column(ForeignKey("audit_inventory_approvals.id"))
    pdf_draft_id: Mapped[int] = mapped_column(ForeignKey("audit_annotation_drafts.id"))
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    excel_draft_id: Mapped[int] = mapped_column(ForeignKey("audit_excel_report_drafts.id"))
    excel_review_event_id: Mapped[int] = mapped_column(ForeignKey("audit_excel_report_review_events.id"))
    excel_sha256: Mapped[str] = mapped_column(String(64))
    unresolved_scope_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
