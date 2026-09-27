"""Job tracking and clinical report retrieval."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from app.jobs import get_job_service
from app.schemas import JobStatus, JobStatusResponse, JobSummary

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _job(job_id: str) -> Dict[str, Any]:
    job = get_job_service().store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    return job


def _summary(job: Dict[str, Any]) -> JobSummary:
    report = job.get("result") or {}
    findings = report.get("genomic_findings") or {}
    return JobSummary(
        job_id=job["job_id"],
        patient_id=job["patient_id"],
        status=job["status"],
        mode=job.get("mode"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
        duration_s=job.get("duration_s"),
        risk_level=(report.get("clinical_prediction") or {}).get("risk_level"),
        identified_genes=findings.get("identified_pathogenic_genes") or [],
        variants_in_panel=findings.get("variants_in_panel"),
        report_id=report.get("report_id"),
    )


@router.get("", response_model=List[JobSummary])
def list_jobs(limit: int = Query(200, ge=1, le=1000)) -> List[JobSummary]:
    """Job history (most recent first), without report bodies."""
    return [_summary(j) for j in get_job_service().store.list()[:limit]]


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
                "message": f"Report unavailable (status={job['status']})",
                "current_step": job.get("current_step"),
                "progress_message": job.get("progress_message"),
                "steps_completed": job.get("steps_completed", []),
                "error": job.get("error"),
            },
        )
    return job["result"]
