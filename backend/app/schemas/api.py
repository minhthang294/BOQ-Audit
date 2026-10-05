from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

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


JobResponse.model_rebuild()
