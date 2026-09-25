from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
