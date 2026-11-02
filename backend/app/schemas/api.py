from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.core.rich_text import sanitize_rich_text
from app.models.entities import JobStatus, OutputType, UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[^\s]+$")
    password: str = Field(min_length=1, max_length=200)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    username: str
    role: UserRole


class AdminUserResponse(UserResponse):
    is_active: bool
    created_at: datetime
    job_count: int = 0


class AdminUpdateUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=160)
    username: str | None = Field(default=None, min_length=3, max_length=80, pattern=r"^[^\s]+$")
    password: SecretStr | None = Field(default=None, min_length=12, max_length=200)
    is_active: bool | None = None

    @field_validator("name", "username", "password", "is_active", mode="before")
    @classmethod
    def normalize_fields(cls, value, info):
        if value is None:
            raise ValueError("Không được để trống trường đã gửi")
        if isinstance(value, str) and info.field_name in {"name", "username"}:
            value = value.strip()
            if info.field_name == "username":
                value = value.lower()
        return value


class AdminCreateUser(AdminUpdateUser):
    name: str = Field(min_length=1, max_length=160)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[^\s]+$")
    password: SecretStr = Field(min_length=12, max_length=200)
    is_active: bool = True


class OutputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_type: OutputType
    original_filename: str
    file_size: int
    created_at: datetime


class EstimateInputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    original_filename: str
    file_size: int
    created_at: datetime


class NarrativeInputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    original_filename: str
    file_size: int
    created_at: datetime


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    job_code: str
    project_name: str
    description: str | None
    original_filename: str
    status: JobStatus
    critical_errors: int
    warnings: int
    customer_notes: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    turnaround_seconds: float
    ai_processing_seconds: float | None
    ai_timing_complete: bool
    current_attempt_seconds: float | None
    audit_runs: list["AuditRunResponse"] = Field(default_factory=list)
    outputs: list[OutputResponse] = Field(default_factory=list)
    estimate_input: EstimateInputResponse | None = None
    narrative_input: NarrativeInputResponse | None = None

    @field_validator("customer_notes", mode="before")
    @classmethod
    def sanitize_customer_notes_for_display(cls, value: str | None) -> str | None:
        return sanitize_rich_text(value) if value is not None else None


class AdminJobResponse(JobResponse):
    admin_notes: str | None
    user: UserResponse


class AuditRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attempt: int
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    status: str


class AttemptArtifactResponse(BaseModel):
    name: str
    size: int


class AttemptArtifactGroup(BaseModel):
    attempt: int
    status: str
    artifacts: list[AttemptArtifactResponse]


class AttemptArtifactsResponse(BaseModel):
    attempts: list[AttemptArtifactGroup]
    legacy: list[AttemptArtifactResponse]


class AdminQualityDetails(BaseModel):
    controller_status: str
    candidate_release_status: str | None
    advisory: bool = True
    enforcing: bool = False
    validator_exit_code: int | None = None
    open_record_count: int | None = None
    open_issue_count: int | None = None
    blocking_checks: list[str] | None = None
    critical_errors: int | None = None
    warnings: int | None = None
    error_code: str | None = None
    missing_outputs: list[str] | None = None


class AdminSanityResponse(BaseModel):
    status: str
    checked_rows: int = 0
    input_digest: str | None = None
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    advisory: bool = True


class AdminStageGateResponse(BaseModel):
    attempt: int
    pass_no: int
    checkpoint_version: int
    snapshot_id: str
    controller_status: str
    candidate_release_status: str | None = None
    blocking_checks: list[str] = Field(default_factory=list)
    error_code: str | None = None
    advanced: bool
    integrity_status: Literal["VERIFIED", "INVALID"]
    created_at: datetime


class AdminReportSourceSummary(BaseModel):
    snapshot_digest: str
    checkpoint_version: int
    contract_version: int = 3
    ledger_record_counts: dict[str, int]
    issue_final_status_counts: dict[str, int]
    issue_candidate_count: int
    pdf_mark_required_issue_count: int
    classified_issue_count: int = 0
    unclassified_issue_count: int = 0
    classification_digest: str | None = None


class AdminQualityResponse(BaseModel):
    job_code: str
    controlled_audit_history: bool = False
    attempt: int | None = None
    stage: str | None = None
    freshness_status: str
    invalidation_event_type: str | None = None
    quality: AdminQualityDetails | None = None
    coverage_status: str = "UNAVAILABLE"
    coverage: dict[str, Any] | None = None
    coverage_limitation: str = "Chưa có expected inventory và work-item ledger đáng tin cậy được lưu cho API này."
    sanity: AdminSanityResponse = AdminSanityResponse(status="UNAVAILABLE")
    stage_gates: list[AdminStageGateResponse] = Field(default_factory=list)
    gate_repair_status: Literal["NOT_APPLICABLE", "GATE_INVALID", "CHECKPOINT_UNAVAILABLE", "NOT_READY", "CONTROLLER_CONTRACT", "VALIDATOR_ERROR", "UNMAPPED_BLOCKER"] = "NOT_APPLICABLE"
    gate_repair_attempts: int = 0
    gate_repair_request_status: str | None = None
    report_source: AdminReportSourceSummary | None = None


class AdminInventoryApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_code: str
    source_digest: str
    reviewer_user_id: int
    reviewer_email: str | None = None
    approved_at: datetime
    is_current: bool
    inventory: dict[str, Any]


class AdminInventoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_code: str
    source_digest: str
    scaffold: dict[str, Any]
    approval: AdminInventoryApprovalResponse | None = None


class AdminInventoryApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory: dict[str, Any]


class AdminGateRepairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_ids: list[str] = Field(min_length=1, max_length=4)


class AdminGateRepairResponse(BaseModel):
    request_id: int
    status: Literal["REQUESTED"]


class AdminIssueClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_status: Literal["CONFIRMED_ERROR", "UNRESOLVED", "FALSE_POSITIVE", "CLOSED_WITH_EXPLANATION", "INCONSISTENT"]
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "NOTE"] | None = None
    reason: str = Field(min_length=12, max_length=2000)


class AdminIssueClassificationResponse(BaseModel):
    issue_id: str
    snapshot_digest: str
    final_status: str
    severity: str | None
    reviewer_user_id: int


class AdminIssueListResponse(BaseModel):
    snapshot_digest: str
    classification_digest: str
    issues: list[dict[str, Any]]
    total: int
    offset: int
    limit: int


class AdminAnnotationDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marks: list[dict[str, Any]] = Field(max_length=500)


class AdminAnnotationDraftResponse(BaseModel):
    draft_id: int
    snapshot_digest: str
    classification_digest: str
    output_pdf_sha256: str
    annotation_qa_status: Literal["PASS", "FAIL", "UNVERIFIED"]
    missing_active_issue_ids: list[str]
    review_counts: dict[str, int] = Field(default_factory=dict)
    marks: list[dict[str, Any]] = Field(default_factory=list)
    is_current: bool


class AdminAnnotationMarkReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    note_crop_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_render_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: Literal["PASS", "FAIL"]
    reason: str = Field(min_length=12, max_length=2000)


class AdminExcelReportReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workbook_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: Literal["PASS", "FAIL"]
    reason: str = Field(min_length=12, max_length=2000)
    reviewed_sections: list[Literal["summary_counts", "ledger_rows", "source_formatting", "severity_colors", "pdf_alignment"]] = Field(min_length=5, max_length=5)

    @field_validator("reviewed_sections")
    @classmethod
    def require_all_review_sections_once(cls, value: list[str]) -> list[str]:
        required = {"summary_counts", "ledger_rows", "source_formatting", "severity_colors", "pdf_alignment"}
        if set(value) != required:
            raise ValueError("all Excel QA sections must be reviewed exactly once")
        return value


class AdminExcelReportReviewResponse(BaseModel):
    draft_id: int
    snapshot_digest: str
    classification_digest: str
    annotation_pdf_sha256: str | None
    workbook_sha256: str
    structural_qa: Literal["PASS", "FAIL"]
    latest_result: Literal["PASS", "FAIL", "UNREVIEWED"]
    latest_reason: str | None
    reviewer_user_id: int | None
    revision_no: int | None
    is_current: bool
    reviewed_sections: list[str] = Field(default_factory=list)


class AdminReleaseSignoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    pdf_draft_id: int = Field(gt=0)
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    excel_draft_id: int = Field(gt=0)
    excel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: Literal["APPROVE_FULL", "APPROVE_PARTIAL", "REJECT"]
    accepted_partial: bool = False
    reviewed_unmarked_pdf: bool = False
    reason: str = Field(min_length=20, max_length=4000)


class AdminReleaseSignoffResponse(BaseModel):
    signoff_id: int
    revision_no: int
    result: Literal["APPROVE_FULL", "APPROVE_PARTIAL", "REJECT"]
    snapshot_digest: str
    classification_digest: str
    checkpoint_version: int
    pdf_draft_id: int
    pdf_sha256: str
    excel_draft_id: int
    excel_sha256: str
    reviewed_unmarked_pdf: bool = False
    unresolved_scope: list[str] = Field(default_factory=list)
    is_current: bool


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int


class CustomerUpdateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_name: str = Field(min_length=1, max_length=240)

    @field_validator("project_name")
    @classmethod
    def normalize_project_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Tên công trình không được để trống")
        return value


class AdminJobListResponse(BaseModel):
    items: list[AdminJobResponse]
    total: int


class AdminUpdateJob(BaseModel):
    status: JobStatus | None = None
    critical_errors: int | None = Field(default=None, ge=0)
    warnings: int | None = Field(default=None, ge=0)
    admin_notes: str | None = Field(default=None, max_length=10000)
    customer_notes: str | None = Field(default=None, max_length=10000)

    @field_validator("customer_notes")
    @classmethod
    def sanitize_customer_notes(cls, value: str | None) -> str | None:
        return sanitize_rich_text(value) if value is not None else None


class MessageResponse(BaseModel):
    message: str


class CodexUsageWindowResponse(BaseModel):
    used_percent: int = Field(ge=0, le=100)
    remaining_percent: int = Field(ge=0, le=100)
    resets_at: int | None = None
    window_duration_minutes: int | None = None


class CodexUsageResponse(BaseModel):
    available: bool
    plan_type: str | None = None
    ordinary_usage_allowed: bool | None = None
    primary: CodexUsageWindowResponse | None = None
    secondary: CodexUsageWindowResponse | None = None
    checked_at: datetime


class AICapacityResponse(BaseModel):
    available: bool
    ready: bool | None
    checked_at: datetime


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Vui lòng nhập câu hỏi")
        return value.strip()


class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    status: str
    created_at: datetime


class ChatResponse(BaseModel):
    enabled: bool
    messages: list[ChatMessageResponse]
    busy: bool


class ExcelSheetInfo(BaseModel):
    name: str
    max_row: int
    max_column: int
    hidden: bool = False


class ExcelWorkbookResponse(BaseModel):
    code: str
    filename: str
    file_size: int
    file_type: str = "xlsx"
    sheets: list[ExcelSheetInfo]
    active_sheet: str
    created_at: datetime


class ExcelCellResponse(BaseModel):
    ref: str
    row: int
    col: int
    value: Any | None = None
    formula: str | None = None
    number_format: str | None = None
    style_id: int = 0
    kind: str = "blank"
    comment: str | None = None


class ExcelCellStyleResponse(BaseModel):
    font_name: str | None = None
    font_size: float | None = None
    bold: bool = False
    italic: bool = False
    underline: str | None = None
    strike: bool = False
    font_color: str | None = None
    fill_color: str | None = None
    horizontal_alignment: str | None = None
    vertical_alignment: str | None = None
    wrap_text: bool = False
    shrink_to_fit: bool = False
    borders: dict[str, dict[str, str | None]] = Field(default_factory=dict)


class ExcelRowDimensionResponse(BaseModel):
    row: int
    height: float | None = None
    hidden: bool = False


class ExcelColumnDimensionResponse(BaseModel):
    col: int
    width: float | None = None
    hidden: bool = False


class ExcelSheetResponse(BaseModel):
    sheet: str
    max_row: int
    max_column: int
    row_start: int
    col_start: int
    row_end: int
    col_end: int
    columns: list[str]
    rows: list[list[ExcelCellResponse]]
    styles: dict[int, ExcelCellStyleResponse] = Field(default_factory=dict)
    row_dimensions: list[ExcelRowDimensionResponse] = Field(default_factory=list)
    column_dimensions: list[ExcelColumnDimensionResponse] = Field(default_factory=list)
    merged: list[str] = Field(default_factory=list)
    truncated: bool = False
    freeze_panes: str | None = None
    auto_filter: str | None = None
    sheet_state: str = "visible"


class ExcelExplainTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ExcelExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet: str = Field(min_length=1, max_length=100)
    scope: Literal["cell", "range", "sheet"] = "cell"
    selection: str = Field(default="", max_length=80)
    question: str = Field(default="", max_length=2000)
    history: list[ExcelExplainTurn] = Field(default_factory=list, max_length=8)

    @field_validator("selection", "question")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()


class ExcelExplainResponse(BaseModel):
    explanation: str
    cells_examined: int
    references_examined: int
    truncated: bool
    evidence_summary: str = ""


class AdminLessonCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_class: str = Field(min_length=1, max_length=120)
    applicability_check_kinds: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=30)
    applicability_target_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=50)
    trigger_conditions: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=30)
    required_checks: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=30)
    counterexamples: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=30)
    source_case_ids: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=50)
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=50)
    project_scope: str = Field(default="unscoped", min_length=1, max_length=160)
    designated_reviewer_email: str | None = Field(default=None, max_length=320)

    @model_validator(mode="after")
    def require_applicability_scope(self):
        if not self.applicability_check_kinds and not self.applicability_target_ids:
            raise ValueError("at least one exact applicability check kind or target ID is required")
        return self


class AdminLessonAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    designated_reviewer_email: str = Field(min_length=3, max_length=320)


class AdminLessonReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: Literal["VERIFIED", "REJECTED"]
    reason: str = Field(min_length=12, max_length=2000)
    evidence_refs: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=50)


class AdminLessonRetireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=12, max_length=2000)


JobResponse.model_rebuild()
