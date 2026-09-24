"""Lancement des analyses (FASTQ par chemin, FASTQ par upload, VCF direct)."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.jobs import get_job_service
from app.schemas import AnalyzeFastqRequest, AnalyzeResponse, AnalyzeVCFRequest, JobStatus
from config.settings import paths
from src.core import context as K
from src.orchestration.engine import describe_plan

router = APIRouter(prefix="/api/v1", tags=["analyses"])

_FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
_PATIENT_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _start(context: dict, mode: str, message: str) -> AnalyzeResponse:
    plan = list(describe_plan(context))
    service = get_job_service()
    job_id = service.store.create(context[K.PATIENT_ID], mode, plan, vcf_path=context.get(K.VCF_URI))
    service.submit(job_id, context)
    return AnalyzeResponse(job_id=job_id, status=JobStatus.QUEUED, patient_id=context[K.PATIENT_ID], plan=plan, message=message)


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
def analyze_fastq(payload: AnalyzeFastqRequest) -> AnalyzeResponse:
    ctx = {K.PATIENT_ID: payload.patient_id, K.FASTQ_R1: payload.fastq_r1, K.FASTQ_R2: payload.fastq_r2,
           K.TRAIN_LLM: payload.train_llm}
    return _start(ctx, "fastq", "Analyse FASTQ démarrée")


@router.post("/analyze/vcf", response_model=AnalyzeResponse, status_code=202)
def analyze_vcf(payload: AnalyzeVCFRequest) -> AnalyzeResponse:
    ctx = {K.PATIENT_ID: payload.patient_id, K.VCF_URI: payload.vcf_path, K.TRAIN_LLM: payload.train_llm}
    return _start(ctx, "vcf", "Analyse VCF démarrée")


def _safe_fastq_name(upload: UploadFile, field: str) -> str:
    name = Path(upload.filename or "").name  # jamais de chemin fourni par le client
    if not name.lower().endswith(_FASTQ_SUFFIXES):
        raise HTTPException(status_code=400, detail=f"{field} : extension FASTQ attendue ({', '.join(_FASTQ_SUFFIXES)})")
    return name


@router.post("/analyze/upload", response_model=AnalyzeResponse, status_code=202)
def analyze_upload(
    patient_id: str = Form(...),
    fastq_r1: UploadFile = File(...),
    fastq_r2: UploadFile = File(...),
    train_llm: bool = Form(False),
) -> AnalyzeResponse:
    pid = patient_id.strip()
    if not _PATIENT_RE.match(pid):
        raise HTTPException(status_code=400, detail="patient_id invalide")
    n1, n2 = _safe_fastq_name(fastq_r1, "fastq_r1"), _safe_fastq_name(fastq_r2, "fastq_r2")
    if n1 == n2:
        raise HTTPException(status_code=400, detail="R1 et R2 doivent être des fichiers distincts")

    # Même disque que patients/ : le rangement final est un simple déplacement
    work = paths().work_dir / "uploads" / pid / str(uuid.uuid4())
    work.mkdir(parents=True, exist_ok=True)
    files = [work / n1, work / n2]
    try:
        for upload, dest in zip((fastq_r1, fastq_r2), files):
            with dest.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh, length=16 << 20)
    except OSError as e:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Écriture des FASTQ impossible : {e}") from e

    service = get_job_service()
    plan = list(describe_plan({K.PATIENT_ID: pid, K.FASTQ_R1: str(files[0]), K.FASTQ_R2: str(files[1]), K.TRAIN_LLM: train_llm}))
    job_id = service.store.create(pid, "fastq", plan)
    service.submit_upload(job_id, pid, files, train_llm)
    return AnalyzeResponse(job_id=job_id, status=JobStatus.QUEUED, patient_id=pid, plan=plan,
                           message="FASTQ reçus — enregistrement sur le serveur puis analyse")
