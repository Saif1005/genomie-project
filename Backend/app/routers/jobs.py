"""Suivi des jobs et récupération du rapport clinique."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.jobs import get_job_service
from app.schemas import JobStatus, JobStatusResponse

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _job(job_id: str) -> Dict[str, Any]:
    job = get_job_service().store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id introuvable")
    return job


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    return JobStatusResponse(**_job(job_id))


@router.get("/{job_id}/report")
def get_report(job_id: str) -> Dict[str, Any]:
    job = _job(job_id)
    if job["status"] != JobStatus.COMPLETED.value or not job.get("result"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Rapport indisponible (status={job['status']})",
                "current_step": job.get("current_step"),
                "progress_message": job.get("progress_message"),
                "steps_completed": job.get("steps_completed", []),
                "error": job.get("error"),
            },
        )
    return job["result"]
