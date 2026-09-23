"""Backend FastAPI production — orchestrateur LangGraph déterministe."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.deployment import deployment_mode, is_local, local_data_root
from config.logging_config import logging_config
from src.agents.assistant_agent import AssistantAgent
from src.storage import get_storage
from src.report.clinical_report_builder import build_clinical_report
from src.workflow.graph_builder import run_genomic_pipeline

logging_config.setup_logging()


def _validate_input_uri(v: str) -> str:
    """URI S3 en mode aws ; chemin sous LOCAL_DATA_ROOT (existant) en mode local."""
    return get_storage().validate_input_uri(v)

# Un seul pipeline GPU à la fois — jobs en thread séparé (ne bloque pas l'API)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalyzeVCFRequest(BaseModel):
    patient_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    vcf_s3: str = Field(..., description="VCF GATK : URI S3 (mode aws) ou chemin serveur (mode local)")

    @field_validator("vcf_s3")
    @classmethod
    def validate_vcf_s3(cls, v: str) -> str:
        return _validate_input_uri(v)


class AnalyzeRequest(BaseModel):
    patient_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    s3_uri_r1: str = Field(..., description="FASTQ R1 : URI S3 (mode aws) ou chemin serveur (mode local)")
    s3_uri_r2: str = Field(..., description="FASTQ R2 : URI S3 (mode aws) ou chemin serveur (mode local)")

    @field_validator("s3_uri_r1", "s3_uri_r2")
    @classmethod
    def validate_s3_uri(cls, v: str) -> str:
        return _validate_input_uri(v)

    @field_validator("s3_uri_r2")
    @classmethod
    def validate_distinct_fastq(cls, v: str, info) -> str:
        r1 = info.data.get("s3_uri_r1", "")
        if r1 and v.strip() == r1.strip():
            raise ValueError("s3_uri_r1 et s3_uri_r2 doivent être distincts")
        return v


class AnalyzeResponse(BaseModel):
    job_id: str
    status: JobStatus
    patient_id: str
    message: str = "Pipeline génomique démarré en arrière-plan"


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    patient_id: str
    created_at: str
    updated_at: str
    mode: Optional[str] = None
    vcf_s3: Optional[str] = None
    current_step: Optional[str] = None
    progress_message: Optional[str] = None
    steps_completed: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None

    model_config = {"extra": "ignore"}


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


_FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")


def _is_fastq_filename(name: str) -> bool:
    lower = (name or "").lower()
    return any(lower.endswith(s) for s in _FASTQ_SUFFIXES)


class _JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()

    def create(self, patient_id: str, r1: str = "", r2: str = "", vcf_s3: str = "") -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "patient_id": patient_id,
                "s3_uri_r1": r1,
                "s3_uri_r2": r2,
                "vcf_s3": vcf_s3,
                "mode": "vcf" if vcf_s3 else "fastq",
                "status": JobStatus.QUEUED,
                "current_step": None,
                "progress_message": "En file d'attente",
                "created_at": now,
                "updated_at": now,
                "steps_completed": [],
                "error": None,
                "result": None,
            }
        return job_id

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            if job_id not in self._jobs:
                return
            self._jobs[job_id].update(kwargs)
            self._jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._jobs[job_id]) if job_id in self._jobs else None


jobs = _JobStore()

_ORCHESTRATOR_STEP_ALIASES = {
    "genomic_pipeline": "parabricks",
    "data_manager": "data_manager",
}


def _use_orchestrator() -> bool:
    """True si l'API doit passer par OrchestratorLangGraph (MCP tools) au lieu du runner direct."""
    explicit = os.getenv("USE_ORCHESTRATOR")
    if explicit is not None:
        return str(explicit).lower() not in ("false", "off", "0", "direct", "pipeline")
    mode = os.getenv("ORCHESTRATOR_MODE", "langgraph")
    return str(mode).lower() not in ("false", "off", "0", "direct", "pipeline")


def _orchestrator_mode_label() -> str:
    if os.getenv("ORCHESTRATOR_DETERMINISTIC", "false").lower() in ("1", "true", "yes"):
        return "LangGraph+MCP (séquence déterministe)"
    return "LangGraph+MCP+Mistral"


app = FastAPI(
    title="Zaynb Genomic Backend",
    description="Pipeline GATK Parabricks + analyse cancer du sein (orchestrateur LangGraph + MCP)",
    version="2.1.0",
)

_cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _genomic_step_label() -> str:
    """Libellé affiché dans l'UI — avertit si le pipeline tourne sur CPU."""
    from src.utils.gpu_manager import select_pipeline_backend

    backend = select_pipeline_backend()
    if backend["backend"] == "cpu":
        return (
            "GATK4 CPU (BWA→MarkDup→BQSR→HaplotypeCaller) — ⚠ sans GPU compatible, "
            f"durée attendue : plusieurs heures ({backend['reason']})"
        )
    return "GATK Parabricks (fq2bam→BQSR→HaplotypeCaller)"


def _make_step_callback(job_id: str) -> Callable:
    genomic_label = _genomic_step_label()
    labels = {
        "data_manager": "Préparation des données" if is_local() else "Téléchargement S3",
        "parabricks": genomic_label,
        "genomic_pipeline": genomic_label,
        "vcf_analysis": "Analyse panel cancer du sein",
        "prediction": "Inférence clinique BioGPT",
        "report": "Génération rapport JSON",
    }

    def on_step(step: str, phase: str, duration_s: Optional[float] = None) -> None:
        step_key = _ORCHESTRATOR_STEP_ALIASES.get(step, step)
        label = labels.get(step_key, labels.get(step, step))
        if phase == "running":
            jobs.update(
                job_id,
                current_step=step_key,
                progress_message=f"En cours : {label}",
            )
            logger.info(f"job={job_id} step={step_key} running")
        elif phase == "completed":
            job = jobs.get(job_id) or {}
            done = list(job.get("steps_completed", []))
            if step_key not in done:
                done.append(step_key)
            jobs.update(
                job_id,
                steps_completed=done,
                progress_message=f"Terminé : {label} ({duration_s:.0f}s)" if duration_s else f"Terminé : {label}",
            )
        elif phase == "failed":
            jobs.update(job_id, progress_message=f"Échec : {label}")

    return on_step


def _finalize_job_from_orchestrator(job_id: str, result) -> None:
    data = result.data or {}
    steps = data.get("steps_done", [])
    success = result.success and "report" in steps
    clinical_report = data.get("clinical_report")
    if not clinical_report:
        ctx_dict = data.get("context") or {}
        clinical_report = build_clinical_report(
            ctx_dict,
            execution_time_seconds=result.execution_time,
            steps_completed=steps,
        ).to_api_dict()
    jobs.update(
        job_id,
        status=JobStatus.COMPLETED if success else JobStatus.FAILED,
        steps_completed=steps,
        current_step=None,
        progress_message="Terminé (orchestrateur)" if success else "Échec (orchestrateur)",
        error=result.error,
        result=clinical_report,
    )


def _run_via_orchestrator(job_id: str, context: Dict[str, Any], config: Dict[str, Any]) -> None:
    from src.agents.orchestrator_langgraph import OrchestratorLangGraph

    jobs.update(
        job_id,
        status=JobStatus.RUNNING,
        progress_message=f"Démarrage {_orchestrator_mode_label()}",
    )
    orchestrator = OrchestratorLangGraph(config=config)
    result = orchestrator.run(context)
    _finalize_job_from_orchestrator(job_id, result)


def _finalize_job(job_id: str, graph_state) -> None:
    success = not graph_state.last_error and "report" in graph_state.steps_completed
    ctx_dict = graph_state.context.to_agent_dict()
    execution_time = sum(
        r.get("execution_time", 0) for r in graph_state.step_results.values()
    )
    clinical_report = ctx_dict.get("clinical_report")
    if not clinical_report:
        clinical_report = build_clinical_report(
            ctx_dict,
            execution_time_seconds=execution_time,
            steps_completed=graph_state.steps_completed,
        ).to_api_dict()
    jobs.update(
        job_id,
        status=JobStatus.COMPLETED if success else JobStatus.FAILED,
        steps_completed=graph_state.steps_completed,
        current_step=None,
        progress_message="Terminé" if success else "Échec",
        error=graph_state.last_error,
        result=clinical_report,
    )


def _run_pipeline_job(job_id: str, payload: AnalyzeRequest) -> None:
    jobs.update(job_id, status=JobStatus.RUNNING, progress_message="Démarrage pipeline FASTQ")
    context = {
        "patient_id": payload.patient_id,
        "fastq_r1": payload.s3_uri_r1,
        "fastq_r2": payload.s3_uri_r2,
        "fastq_r1_s3": payload.s3_uri_r1,
        "fastq_r2_s3": payload.s3_uri_r2,
        "instance_id": os.getenv("EC2_INSTANCE_ID"),
        "ssh_key": os.getenv("SSH_KEY_PATH"),
        "s3_bucket": os.getenv("S3_INPUT_BUCKET"),
    }
    config = {
        "instance_id": context["instance_id"],
        "ssh_key": context["ssh_key"],
        "on_step": _make_step_callback(job_id),
    }
    try:
        if _use_orchestrator():
            _run_via_orchestrator(job_id, context, config)
        else:
            graph_state = run_genomic_pipeline(context, config=config, use_langgraph=True)
            _finalize_job(job_id, graph_state)
    except Exception as e:
        logger.exception(f"job={job_id} failed")
        jobs.update(job_id, status=JobStatus.FAILED, error=str(e), progress_message="Erreur")


def _run_vcf_job(job_id: str, payload: AnalyzeVCFRequest) -> None:
    jobs.update(job_id, status=JobStatus.RUNNING, progress_message="Démarrage workflow VCF")
    context = {
        "patient_id": payload.patient_id,
        "vcf_s3": payload.vcf_s3,
        "instance_id": os.getenv("EC2_INSTANCE_ID"),
        "ssh_key": os.getenv("SSH_KEY_PATH"),
    }
    config = {
        "instance_id": context["instance_id"],
        "ssh_key": context["ssh_key"],
        "on_step": _make_step_callback(job_id),
    }
    try:
        if _use_orchestrator():
            _run_via_orchestrator(job_id, context, config)
        else:
            graph_state = run_genomic_pipeline(
                context, config=config, use_langgraph=False
            )
            _finalize_job(job_id, graph_state)
    except Exception as e:
        logger.exception(f"job={job_id} vcf failed")
        jobs.update(job_id, status=JobStatus.FAILED, error=str(e), progress_message="Erreur")


def _submit(fn, *args) -> None:
    """Soumet le job dans un thread — l'API reste responsive."""
    _executor.submit(fn, *args)


def _run_upload_then_pipeline(
    job_id: str,
    patient_id: str,
    path_r1: str,
    path_r2: str,
) -> None:
    """Range les FASTQ uploadés (S3 en mode aws, patients/<ID>/input en local) puis lance le pipeline."""
    storage = get_storage()
    jobs.update(
        job_id,
        status=JobStatus.RUNNING,
        progress_message="Enregistrement FASTQ" if is_local() else "Upload FASTQ → S3",
    )
    try:
        r1_key = storage.key_for(patient_id, "input", Path(path_r1).name)
        r2_key = storage.key_for(patient_id, "input", Path(path_r2).name)
        # move=True en local : évite de dupliquer des FASTQ de plusieurs Go
        r1_s3 = storage.put(path_r1, r1_key, area="input", move=True)
        r2_s3 = storage.put(path_r2, r2_key, area="input", move=True)
        jobs.update(job_id, progress_message="FASTQ enregistrés — démarrage pipeline")
        payload = AnalyzeRequest(
            patient_id=patient_id,
            s3_uri_r1=r1_s3,
            s3_uri_r2=r2_s3,
        )
        _run_pipeline_job(job_id, payload)
    except Exception as e:
        logger.exception(f"job={job_id} upload failed")
        jobs.update(job_id, status=JobStatus.FAILED, error=str(e), progress_message="Échec upload")
    finally:
        for p in (path_r1, path_r2):
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


_assistant = AssistantAgent()


@app.post("/api/v1/assistant/chat", response_model=AssistantChatResponse)
async def assistant_chat(payload: AssistantChatRequest) -> AssistantChatResponse:
    """Assistant IA — comprend les prompts humains et peut lancer le workflow."""
    parsed = _assistant.process(
        payload.message,
        [m.model_dump() for m in payload.history],
        payload.context,
    )
    intent = parsed.get("intent", "chat")
    missing = list(parsed.get("missing_fields") or [])
    job_id: Optional[str] = None
    action_taken: Optional[str] = None
    reply = parsed.get("reply", "")

    if intent == "start_fastq" and not missing:
        r1 = parsed.get("s3_uri_r1")
        r2 = parsed.get("s3_uri_r2")
        pid = parsed.get("patient_id")
        if r1 and r2 and pid:
            req = AnalyzeRequest(patient_id=pid, s3_uri_r1=r1, s3_uri_r2=r2)
            job_id = jobs.create(pid, req.s3_uri_r1, req.s3_uri_r2)
            _submit(_run_pipeline_job, job_id, req)
            action_taken = "started_fastq"
            reply = (
                f"{reply} Job créé ({job_id}). Pipeline FASTQ démarré pour {pid}."
            ).strip()

    elif intent == "start_vcf" and not missing:
        pid = parsed.get("patient_id")
        vcf = parsed.get("vcf_s3")
        if pid and vcf:
            req = AnalyzeVCFRequest(patient_id=pid, vcf_s3=vcf)
            job_id = jobs.create(pid, vcf_s3=vcf)
            _submit(_run_vcf_job, job_id, req)
            action_taken = "started_vcf"
            reply = (
                f"{reply} Job VCF créé ({job_id}) pour {pid}."
            ).strip()

    elif intent == "job_status":
        jid = parsed.get("job_id") or payload.context.get("job_id")
        if jid:
            job = jobs.get(jid)
            if job:
                action_taken = "job_status"
                reply = (
                    f"Job {jid} — statut : {job['status']}. "
                    f"Étape : {job.get('current_step') or '—'}. "
                    f"{job.get('progress_message') or ''}"
                ).strip()
            else:
                reply = f"Aucun job trouvé pour l'identifiant {jid}."
        else:
            reply = parsed.get("reply") or "Indiquez le job_id (UUID) à consulter."

    return AssistantChatResponse(
        reply=reply,
        intent=intent,
        action_taken=action_taken,
        job_id=job_id,
        patient_id=parsed.get("patient_id"),
        missing_fields=missing,
        parsed={k: v for k, v in parsed.items() if k != "reply"},
    )


@app.post("/api/v1/analyze/upload", response_model=AnalyzeResponse, status_code=202)
async def analyze_upload(
    patient_id: str = Form(...),
    fastq_r1: UploadFile = File(...),
    fastq_r2: UploadFile = File(...),
) -> AnalyzeResponse:
    """Upload direct FASTQ depuis le front-end → S3 → pipeline."""
    pid = patient_id.strip()
    if not re.match(r"^[A-Za-z0-9_\-]+$", pid):
        raise HTTPException(status_code=400, detail="patient_id invalide")

    if not _is_fastq_filename(fastq_r1.filename or ""):
        raise HTTPException(status_code=400, detail="fastq_r1 : extension FASTQ attendue")
    if not _is_fastq_filename(fastq_r2.filename or ""):
        raise HTTPException(status_code=400, detail="fastq_r2 : extension FASTQ attendue")
    if fastq_r1.filename == fastq_r2.filename:
        raise HTTPException(status_code=400, detail="R1 et R2 doivent être des fichiers distincts")

    if is_local():
        from config.runtime_config import runtime_config

        # Même système de fichiers que patients/ → déplacement instantané
        work_root = runtime_config.work_mount / "uploads"
    else:
        work_root = Path(os.getenv("RUNTIME_WORK_MOUNT", tempfile.gettempdir())) / "uploads"
    work_dir = work_root / pid / str(uuid.uuid4())
    work_dir.mkdir(parents=True, exist_ok=True)

    path_r1 = work_dir / (fastq_r1.filename or "R1.fastq.gz")
    path_r2 = work_dir / (fastq_r2.filename or "R2.fastq.gz")

    try:
        with path_r1.open("wb") as f:
            shutil.copyfileobj(fastq_r1.file, f)
        with path_r2.open("wb") as f:
            shutil.copyfileobj(fastq_r2.file, f)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Échec écriture fichiers: {e}") from e

    job_id = jobs.create(pid)
    _submit(_run_upload_then_pipeline, job_id, pid, str(path_r1), str(path_r2))

    return AnalyzeResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        patient_id=pid,
        message=(
            "FASTQ reçus — enregistrement local et pipeline en cours"
            if is_local()
            else "FASTQ reçus — upload S3 et pipeline en cours"
        ),
    )


@app.post("/api/v1/analyze/vcf", response_model=AnalyzeResponse, status_code=202)
async def analyze_vcf(payload: AnalyzeVCFRequest) -> AnalyzeResponse:
    job_id = jobs.create(payload.patient_id, vcf_s3=payload.vcf_s3)
    _submit(_run_vcf_job, job_id, payload)
    return AnalyzeResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        patient_id=payload.patient_id,
        message=(
            f"Workflow VCF via {_orchestrator_mode_label()} "
            "(vcf_analysis → prediction → report)"
            if _use_orchestrator()
            else "Workflow VCF démarré (vcf_analysis → prediction → report)"
        ),
    )


@app.post("/api/v1/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze_patient(payload: AnalyzeRequest) -> AnalyzeResponse:
    job_id = jobs.create(payload.patient_id, payload.s3_uri_r1, payload.s3_uri_r2)
    _submit(_run_pipeline_job, job_id, payload)
    return AnalyzeResponse(
        job_id=job_id,
        status=JobStatus.QUEUED,
        patient_id=payload.patient_id,
    )


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id introuvable")
    return JobStatusResponse(**job)


@app.get("/api/v1/jobs/{job_id}/report")
async def get_job_clinical_report(job_id: str) -> Dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id introuvable")
    if job["status"] != JobStatus.COMPLETED or not job.get("result"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Rapport indisponible (status={job['status']})",
                "current_step": job.get("current_step"),
                "progress_message": job.get("progress_message"),
                "steps_completed": job.get("steps_completed", []),
            },
        )
    return job["result"]


@app.get("/health")
async def health() -> Dict[str, Any]:
    from src.utils.gpu_manager import select_pipeline_backend

    backend = select_pipeline_backend()
    return {
        "status": "ok",
        "service": "zaynb-genomic-backend",
        "orchestrator": _orchestrator_mode_label() if _use_orchestrator() else "direct",
        "use_orchestrator": _use_orchestrator(),
        "deployment_mode": deployment_mode(),
        "data_root": str(local_data_root()) if is_local() else None,
        "pipeline_backend": backend["backend"],
        "pipeline_backend_reason": backend["reason"],
        "gpus": backend["gpus"],
    }
