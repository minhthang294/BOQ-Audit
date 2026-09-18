from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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


class AdminJobResponse(JobResponse):
    admin_notes: str | None
    user: UserResponse


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int


class AdminJobListResponse(BaseModel):
    items: list[AdminJobResponse]
    total: int


class AdminUpdateJob(BaseModel):
    status: JobStatus | None = None
    critical_errors: int | None = Field(default=None, ge=0)
    warnings: int | None = Field(default=None, ge=0)
    admin_notes: str | None = Field(default=None, max_length=10000)
    customer_notes: str | None = Field(default=None, max_length=10000)


class MessageResponse(BaseModel):
    message: str
