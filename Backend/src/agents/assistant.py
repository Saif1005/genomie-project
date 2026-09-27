"""Conversational assistant — understanding human prompts (Mistral via Ollama)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from src.llm.ollama_client import OllamaClient

# Server paths to FASTQ/VCF files (e.g. /data/germlineiq/patients/P1/input/R1.fastq.gz)
_LOCAL_PATH_RE = re.compile(r"(?<![\w:])/[\w.\-/]+\.(?:fastq|fq|vcf)(?:\.gz)?\b", re.IGNORECASE)
_PATIENT_RE = re.compile(r"\b(PATIENT\d+|[A-Za-z][A-Za-z0-9_\-]{2,31})\b")
_JOB_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)

ASSISTANT_SYSTEM = """You are the clinical assistant of the GermlineIQ multi-agent system (hereditary breast cancer).

You understand English and French. You help clinicians to:
- start a FASTQ analysis (GATK/Parabricks → ClinVar annotation → risk → report)
- start a VCF-only analysis (path on the server)
- explain the pipeline and the agents
- check the status of a job

Reply ONLY with valid JSON (no markdown):
{
  "intent": "start_fastq|start_vcf|explain_pipeline|job_status|help|chat",
  "patient_id": null,
  "fastq_r1": null,
  "fastq_r2": null,
  "vcf_path": null,
  "job_id": null,
  "reply": "short natural reply in English",
  "missing_fields": []
}

Rules:
- start_fastq: patient_id + fastq_r1 + fastq_r2 required (otherwise list missing_fields)
- start_vcf: patient_id + vcf_path required
- job_status: extract the job_id UUID if mentioned
- explain_pipeline / help: never start anything
- reply: professional, clear, biomedical tone
"""


class AssistantAgent:
    """Parses user prompts and produces a structured action."""

    def __init__(self) -> None:
        self.ollama = OllamaClient()

    def process(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        history = history or []
        context = context or {}
        parsed = self._parse_with_llm(message, history, context)
        if not parsed:
            parsed = self._parse_heuristic(message, context)
        parsed = self._merge_context(parsed, context)
        parsed["reply"] = parsed.get("reply") or self._default_reply(parsed)
        return parsed

    def _parse_with_llm(
        self,
        message: str,
        history: List[Dict[str, str]],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        ctx_lines = []
        if context.get("patient_id"):
            ctx_lines.append(f"known patient_id: {context['patient_id']}")
        if context.get("pending_upload"):
            ctx_lines.append("FASTQ files attached in the UI (direct upload possible)")
        if context.get("job_id"):
            ctx_lines.append(f"active job: {context['job_id']}")

        hist_text = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-6:]
        )
        prompt = (
            f"Session context:\n{chr(10).join(ctx_lines) or 'none'}\n\n"
            f"History:\n{hist_text or 'empty'}\n\n"
            f"User message:\n{message}"
        )
        raw = self.ollama.generate(prompt, system=ASSISTANT_SYSTEM)
        if not raw.strip():
            return None
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start < 0 or end <= start:
                return None
            data = json.loads(raw[start:end])
            if "intent" not in data:
                return None
            return data
        except json.JSONDecodeError as e:
            logger.warning(f"Assistant JSON parse failed: {e}")
            return None

    def _parse_heuristic(
        self, message: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        lower = message.lower()
        uris = _LOCAL_PATH_RE.findall(message)
        patient = _PATIENT_RE.search(message)
        job = _JOB_RE.search(message)
        patient_id = patient.group(1) if patient else context.get("patient_id")

        if any(w in lower for w in ("statut", "status", "job", "avancement", "progression")):
            return {
                "intent": "job_status",
                "patient_id": patient_id,
                "job_id": job.group(1) if job else context.get("job_id"),
                "reply": "",
                "missing_fields": [] if (job or context.get("job_id")) else ["job_id"],
            }

        if any(w in lower for w in ("aide", "help", "comment", "how")):
            return {"intent": "help", "reply": "", "missing_fields": []}

        if any(
            w in lower
            for w in ("pipeline", "agent", "parabricks", "biogpt", "orchest", "explique")
        ):
            return {"intent": "explain_pipeline", "reply": "", "missing_fields": []}

        vcf_uris = [u for u in uris if ".vcf" in u.lower()]
        fastq_uris = [u for u in uris if u not in vcf_uris]

        if vcf_uris or ("vcf" in lower and uris):
            vcf_path = vcf_uris[0] if vcf_uris else (uris[0] if uris else None)
            missing = []
            if not patient_id:
                missing.append("patient_id")
            if not vcf_path:
                missing.append("vcf_path")
            return {
                "intent": "start_vcf",
                "patient_id": patient_id,
                "vcf_path": vcf_path,
                "reply": "",
                "missing_fields": missing,
            }

        if (
            any(w in lower for w in ("lance", "lancer", "analys", "démarre", "start", "run"))
            or fastq_uris
            or context.get("pending_upload")
        ):
            r1 = fastq_uris[0] if len(fastq_uris) > 0 else None
            r2 = fastq_uris[1] if len(fastq_uris) > 1 else None
            missing = []
            if not patient_id:
                missing.append("patient_id")
            if not r1 and not context.get("pending_upload"):
                missing.append("fastq_r1")
            if not r2 and not context.get("pending_upload"):
                missing.append("fastq_r2")
            return {
                "intent": "start_fastq",
                "patient_id": patient_id,
                "fastq_r1": r1,
                "fastq_r2": r2,
                "reply": "",
                "missing_fields": missing,
            }

        return {
            "intent": "chat",
            "patient_id": patient_id,
            "reply": "",
            "missing_fields": [],
        }

    def _merge_context(
        self, parsed: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        for key in ("patient_id", "job_id", "fastq_r1", "fastq_r2", "vcf_path"):
            if not parsed.get(key) and context.get(key):
                parsed[key] = context[key]
        if context.get("pending_upload") and parsed.get("intent") == "start_fastq":
            missing = list(parsed.get("missing_fields") or [])
            parsed["missing_fields"] = [
                f for f in missing if f not in ("fastq_r1", "fastq_r2")
            ]
            if not parsed.get("patient_id"):
                parsed.setdefault("missing_fields", []).append("patient_id")
        return parsed

    def _default_reply(self, parsed: Dict[str, Any]) -> str:
        intent = parsed.get("intent", "chat")
        missing = parsed.get("missing_fields") or []
        if missing:
            labels = {
                "patient_id": "patient identifier",
                "fastq_r1": "FASTQ R1 (server path or attached file)",
                "fastq_r2": "FASTQ R2 (server path or attached file)",
                "vcf_path": "VCF path on the server",
                "job_id": "job identifier (UUID)",
            }
            need = ", ".join(labels.get(m, m) for m in missing)
            return f"To continue, I need: {need}."
        replies = {
            "start_fastq": "Starting the FASTQ analysis through the multi-agent orchestrator.",
            "start_vcf": "Starting the VCF analysis (ClinVar annotation → panel → risk → report).",
            "explain_pipeline": (
                "The GermlineIQ pipeline chains: FASTQ preparation, GATK variant calling "
                "(Parabricks on GPU or GATK4 on CPU) restricted to the panel, ClinVar annotation, "
                "quality control and statistics, rule-based risk level and the clinical report. "
                "BioGPT only adds a verified literature commentary, never used for decisions."
            ),
            "help": (
                "You can: attach FASTQ files and start the analysis, enter server paths "
                "(/data/germlineiq/patients/<ID>/input/…), or ask me in natural language "
                "(e.g. \"Run PATIENT001 with /data/germlineiq/patients/PATIENT001/input/R1.fastq.gz and R2…\")."
            ),
            "job_status": "Checking the job status.",
            "chat": "How can I help with the genomic analysis?",
        }
        return replies.get(intent, replies["chat"])
