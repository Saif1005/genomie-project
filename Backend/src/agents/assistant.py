"""Assistant conversationnel — compréhension des prompts humains (Mistral/Ollama)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from src.llm.ollama_client import OllamaClient

# Chemins serveur vers FASTQ/VCF (ex. /data/zaynb/patients/P1/input/R1.fastq.gz)
_LOCAL_PATH_RE = re.compile(r"(?<![\w:])/[\w.\-/]+\.(?:fastq|fq|vcf)(?:\.gz)?\b", re.IGNORECASE)
_PATIENT_RE = re.compile(r"\b(PATIENT\d+|[A-Za-z][A-Za-z0-9_\-]{2,31})\b")
_JOB_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)

ASSISTANT_SYSTEM = """Tu es l'assistant clinique du système multi-agents Zaynb (cancer du sein).

Tu comprends le français et l'anglais. Tu aides les cliniciens à :
- lancer une analyse FASTQ (GATK/Parabricks → annotation ClinVar → risque → rapport)
- lancer une analyse VCF seule (chemin sur le serveur)
- expliquer le pipeline et les agents
- consulter le statut d'un job

Réponds UNIQUEMENT avec un JSON valide (sans markdown) :
{
  "intent": "start_fastq|start_vcf|explain_pipeline|job_status|help|chat",
  "patient_id": null,
  "fastq_r1": null,
  "fastq_r2": null,
  "vcf_path": null,
  "job_id": null,
  "reply": "réponse naturelle courte en français",
  "missing_fields": []
}

Règles :
- start_fastq : patient_id + fastq_r1 + fastq_r2 requis (ou indiquer missing_fields)
- start_vcf : patient_id + vcf_path requis
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
                "patient_id": "identifiant patient",
                "fastq_r1": "FASTQ R1 (chemin serveur ou fichier attaché)",
                "fastq_r2": "FASTQ R2 (chemin serveur ou fichier attaché)",
                "vcf_path": "chemin du VCF sur le serveur",
                "job_id": "identifiant du job (UUID)",
            }
            need = ", ".join(labels.get(m, m) for m in missing)
            return f"Pour continuer, j'ai besoin de : {need}."
        replies = {
            "start_fastq": "Je lance l'analyse FASTQ via l'orchestrateur multi-agents.",
            "start_vcf": "Je lance l'analyse VCF (annotation ClinVar → panel → risque → rapport).",
            "explain_pipeline": (
                "Le pipeline ZAYNB enchaîne : préparation des FASTQ, appel de variants GATK "
                "(Parabricks sur GPU ou GATK4 sur CPU) restreint au panel, annotation ClinVar, "
                "contrôle qualité, niveau de risque par règles explicites et rapport clinique. "
                "BioGPT n'ajoute qu'un commentaire bibliographique, non décisionnel."
            ),
            "help": (
                "Vous pouvez : attacher des FASTQ et lancer l'analyse, saisir des chemins sur le serveur "
                "(/data/zaynb/patients/<ID>/input/…), ou me demander en langage naturel "
                "(ex. « Lance PATIENT001 avec /data/zaynb/patients/PATIENT001/input/R1.fastq.gz et R2… »)."
            ),
            "job_status": "Je consulte le statut du job.",
            "chat": "Comment puis-je vous aider pour l'analyse génomique ?",
        }
        return replies.get(intent, replies["chat"])
