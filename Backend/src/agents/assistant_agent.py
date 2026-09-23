"""Assistant conversationnel — compréhension des prompts humains (Mistral/Ollama)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from src.llm.ollama_client import OllamaClient

_S3_RE = re.compile(r"s3://[a-z0-9.\-]+/\S+", re.IGNORECASE)
# Mode local : chemins serveur vers FASTQ/VCF (ex. /data/zaynb/patients/P1/input/R1.fastq.gz)
_LOCAL_PATH_RE = re.compile(r"(?<![\w:])/[\w.\-/]+\.(?:fastq|fq|vcf)(?:\.gz)?\b", re.IGNORECASE)
_PATIENT_RE = re.compile(r"\b(PATIENT\d+|[A-Za-z][A-Za-z0-9_\-]{2,31})\b")
_JOB_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)

ASSISTANT_SYSTEM = """Tu es l'assistant clinique du système multi-agents Zaynb (cancer du sein).

Tu comprends le français et l'anglais. Tu aides les cliniciens à :
- lancer une analyse FASTQ (Parabricks GATK → VCF → BioGPT)
- lancer une analyse VCF seule
- expliquer le pipeline et les agents
- consulter le statut d'un job

Réponds UNIQUEMENT avec un JSON valide (sans markdown) :
{
  "intent": "start_fastq|start_vcf|explain_pipeline|job_status|help|chat",
  "patient_id": null,
  "s3_uri_r1": null,
  "s3_uri_r2": null,
  "vcf_s3": null,
  "job_id": null,
  "reply": "réponse naturelle courte en français",
  "missing_fields": []
}

Règles :
- start_fastq : patient_id + s3_uri_r1 + s3_uri_r2 requis (ou indiquer missing_fields)
- start_vcf : patient_id + vcf_s3 requis
- job_status : extraire job_id UUID si mentionné
- explain_pipeline / help : pas de lancement
- reply : ton professionnel, clair, biomédical
"""


class AssistantAgent:
    """Parse les prompts utilisateur et produit une action structurée."""

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
            ctx_lines.append(f"patient_id connu: {context['patient_id']}")
        if context.get("pending_upload"):
            ctx_lines.append("fichiers FASTQ attachés côté UI (upload direct possible)")
        if context.get("job_id"):
            ctx_lines.append(f"job actif: {context['job_id']}")

        hist_text = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in history[-6:]
        )
        prompt = (
            f"Contexte session:\n{chr(10).join(ctx_lines) or 'aucun'}\n\n"
            f"Historique:\n{hist_text or 'vide'}\n\n"
            f"Message utilisateur:\n{message}"
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
        s3_uris = _S3_RE.findall(message) + _LOCAL_PATH_RE.findall(message)
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

        vcf_uris = [u for u in s3_uris if ".vcf" in u.lower()]
        fastq_uris = [u for u in s3_uris if u not in vcf_uris]

        if vcf_uris or ("vcf" in lower and s3_uris):
            vcf_s3 = vcf_uris[0] if vcf_uris else (s3_uris[0] if s3_uris else None)
            missing = []
            if not patient_id:
                missing.append("patient_id")
            if not vcf_s3:
                missing.append("vcf_s3")
            return {
                "intent": "start_vcf",
                "patient_id": patient_id,
                "vcf_s3": vcf_s3,
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
                missing.append("s3_uri_r1")
            if not r2 and not context.get("pending_upload"):
                missing.append("s3_uri_r2")
            return {
                "intent": "start_fastq",
                "patient_id": patient_id,
                "s3_uri_r1": r1,
                "s3_uri_r2": r2,
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
        for key in ("patient_id", "job_id", "s3_uri_r1", "s3_uri_r2", "vcf_s3"):
            if not parsed.get(key) and context.get(key):
                parsed[key] = context[key]
        if context.get("pending_upload") and parsed.get("intent") == "start_fastq":
            missing = list(parsed.get("missing_fields") or [])
            parsed["missing_fields"] = [
                f for f in missing if f not in ("s3_uri_r1", "s3_uri_r2")
            ]
            if not parsed.get("patient_id"):
                parsed.setdefault("missing_fields", []).append("patient_id")
        return parsed

    def _default_reply(self, parsed: Dict[str, Any]) -> str:
        intent = parsed.get("intent", "chat")
        missing = parsed.get("missing_fields") or []
        if missing:
            labels = {
                "patient_id": "identifiant patient",
                "s3_uri_r1": "FASTQ R1 (S3 ou fichier attaché)",
                "s3_uri_r2": "FASTQ R2 (S3 ou fichier attaché)",
                "vcf_s3": "chemin du VCF (S3 ou serveur)",
                "job_id": "identifiant du job (UUID)",
            }
            need = ", ".join(labels.get(m, m) for m in missing)
            return f"Pour continuer, j'ai besoin de : {need}."
        replies = {
            "start_fastq": "Je lance l'analyse FASTQ via l'orchestrateur multi-agents.",
            "start_vcf": "Je lance le workflow VCF (analyse → BioGPT → rapport).",
            "explain_pipeline": (
                "Le pipeline Zaynb enchaîne : téléchargement S3, alignement Parabricks GATK, "
                "analyse du panel cancer du sein, inférence BioGPT et génération du rapport clinique."
            ),
            "help": (
                "Vous pouvez : attacher des FASTQ et lancer l'analyse, saisir des URIs S3, "
                "ou me demander en langage naturel (ex. « Lance PATIENT001 avec s3://… »)."
            ),
            "job_status": "Je consulte le statut du job.",
            "chat": "Comment puis-je vous aider pour l'analyse génomique ?",
        }
        return replies.get(intent, replies["chat"])
