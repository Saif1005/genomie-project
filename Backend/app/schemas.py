"""Modèles d'entrée/sortie de l'API REST.

Les noms historiques (s3_uri_r1, s3_uri_r2, vcf_s3) restent acceptés en entrée (alias).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from src.storage import get_storage

_PATIENT_ID = r"^[A-Za-z0-9_\-]+$"


def _server_path(v: str) -> str:
    """Chemin existant sous LOCAL_DATA_ROOT (les chemins hors racine sont refusés)."""
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
            raise ValueError("fastq_r1 et fastq_r2 doivent être distincts")
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
    message: str = "Analyse démarrée"


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
