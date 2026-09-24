"""Assistant conversationnel : comprend la demande et peut lancer ou suivre une analyse."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import ValidationError

from app.jobs import get_job_service
from app.routers.analysis import _start
from app.schemas import AnalyzeFastqRequest, AnalyzeVCFRequest, AssistantChatRequest, AssistantChatResponse
from src.agents.assistant import AssistantAgent
from src.core import context as K

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])
_assistant = AssistantAgent()


@router.post("/chat", response_model=AssistantChatResponse)
def chat(payload: AssistantChatRequest) -> AssistantChatResponse:
    parsed = _assistant.process(payload.message, [m.model_dump() for m in payload.history], payload.context)
    intent = parsed.get("intent", "chat")
    missing = list(parsed.get("missing_fields") or [])
    reply = parsed.get("reply", "")
    job_id: Optional[str] = None
    action: Optional[str] = None

    try:
        if intent == "start_fastq" and not missing and parsed.get("fastq_r1") and parsed.get("fastq_r2"):
            req = AnalyzeFastqRequest(patient_id=parsed["patient_id"], fastq_r1=parsed["fastq_r1"], fastq_r2=parsed["fastq_r2"])
            started = _start({K.PATIENT_ID: req.patient_id, K.FASTQ_R1: req.fastq_r1, K.FASTQ_R2: req.fastq_r2}, "fastq", "")
            job_id, action = started.job_id, "started_fastq"
            reply = f"{reply} Job {job_id} créé : {' → '.join(started.plan)}.".strip()
        elif intent == "start_vcf" and not missing and parsed.get("vcf_path"):
            req = AnalyzeVCFRequest(patient_id=parsed["patient_id"], vcf_path=parsed["vcf_path"])
            started = _start({K.PATIENT_ID: req.patient_id, K.VCF_URI: req.vcf_path}, "vcf", "")
            job_id, action = started.job_id, "started_vcf"
            reply = f"{reply} Job {job_id} créé : {' → '.join(started.plan)}.".strip()
        elif intent == "job_status":
            jid = parsed.get("job_id") or payload.context.get("job_id")
            job = get_job_service().store.get(jid) if jid else None
            if job:
                action = "job_status"
                reply = (f"Job {jid} — statut : {job['status']}. Étape : {job.get('current_step') or '—'}. "
                         f"{job.get('progress_message') or ''}").strip()
            else:
                reply = f"Aucun job trouvé pour {jid}." if jid else "Indiquez l'identifiant du job (UUID)."
    except ValidationError as e:
        reply = "Impossible de lancer l'analyse : " + "; ".join(err["msg"] for err in e.errors())

    return AssistantChatResponse(
        reply=reply, intent=intent, action_taken=action, job_id=job_id,
        patient_id=parsed.get("patient_id"), missing_fields=missing,
        parsed={k: v for k, v in parsed.items() if k != "reply"},
    )
