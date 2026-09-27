"""REST API input/output models.

Historical names (s3_uri_r1, s3_uri_r2, vcf_s3) are still accepted as input aliases.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from src.storage import get_storage

_PATIENT_ID = r"^[A-Za-z0-9_\-]+$"


def _server_path(v: str) -> str:
    """Existing path under LOCAL_DATA_ROOT (paths outside the root are rejected)."""
    return get_storage().validate_input_uri(v)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalyzeFastqRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., min_length=1, max_length=64, pattern=_PATIENT_ID)
    fastq_r1: str = Field(..., validation_alias=AliasChoices("fastq_r1", "s3_uri_r1"))
    fastq_r2: str = Field(..., validation_alias=AliasChoices("fastq_r2", "s3_uri_r2"))
    train_llm: bool = False

    @field_validator("fastq_r1", "fastq_r2")
    @classmethod
    def _exists(cls, v: str) -> str:
        return _server_path(v)

    @model_validator(mode="after")
    def _distinct(self) -> "AnalyzeFastqRequest":
        if self.fastq_r1 == self.fastq_r2:
            raise ValueError("fastq_r1 and fastq_r2 must be different files")
        return self


class AnalyzeVCFRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., min_length=1, max_length=64, pattern=_PATIENT_ID)
    vcf_path: str = Field(..., validation_alias=AliasChoices("vcf_path", "vcf_s3"))
    train_llm: bool = False

    @field_validator("vcf_path")
    @classmethod
    def _exists(cls, v: str) -> str:
        return _server_path(v)


class AnalyzeResponse(BaseModel):
    job_id: str
    status: JobStatus
    patient_id: str
    plan: List[str] = Field(default_factory=list)
    message: str = "Analysis started"


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str
    status: JobStatus
    patient_id: str
    created_at: str
    updated_at: str
    mode: Optional[str] = None
    vcf_path: Optional[str] = None
    plan: List[str] = Field(default_factory=list)
    current_step: Optional[str] = None
    progress_message: Optional[str] = None
    steps_completed: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    # Per-agent execution record (tool, ui_step, status completed|cached|failed, duration in s)
    step_timings: List[Dict[str, Any]] = Field(default_factory=list)
    duration_s: Optional[float] = None
    router: Optional[str] = None


class JobSummary(BaseModel):
    """One line of the job history (no report body)."""

    job_id: str
    patient_id: str
    status: JobStatus
    mode: Optional[str] = None
    created_at: str
    updated_at: str
    duration_s: Optional[float] = None
    risk_level: Optional[str] = None
    identified_genes: List[str] = Field(default_factory=list)
    variants_in_panel: Optional[int] = None
    report_id: Optional[str] = None


class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(user|assistant|system)$")
    content: str = Field(..., min_length=1, max_length=8000)


class AssistantChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: List[ChatMessage] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class AssistantChatResponse(BaseModel):
    reply: str
    intent: str
    action_taken: Optional[str] = None
    job_id: Optional[str] = None
    patient_id: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)
    parsed: Dict[str, Any] = Field(default_factory=dict)
