"""Clés du contexte partagé entre agents.

Le contexte est un dict JSON-sérialisable : chaque agent lit ses clés d'entrée et renvoie
ses clés de sortie. Les noms sont centralisés ici pour que le registre d'outils (requires /
produces), les agents et l'API parlent le même vocabulaire.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# Entrées utilisateur
PATIENT_ID = "patient_id"
FASTQ_R1 = "fastq_r1"
FASTQ_R2 = "fastq_r2"
VCF_URI = "vcf_uri"            # VCF fourni (mode VCF) ou produit par l'appel de variants
TRAIN_LLM = "train_llm"

# Produits des agents
FASTQ_R1_URI = "fastq_r1_uri"
FASTQ_R2_URI = "fastq_r2_uri"
BAM_URI = "bam_uri"
PIPELINE_BACKEND = "pipeline_backend"
ANNOTATED_VARIANTS = "annotated_variants_path"
ANNOTATION = "annotation"
PANEL_ANALYSIS = "panel_analysis"
VCF_METRICS = "vcf_metrics"
RISK_ASSESSMENT = "risk_assessment"
PREDICTION_RESULTS = "prediction_results"
CLINICAL_REPORT = "clinical_report"
REPORT_URI = "report_uri"
TRAINING_DATA = "training_data_path"

# Métadonnées d'exécution ajoutées par le moteur
STEPS_COMPLETED = "steps_completed"
EXECUTION_TIME = "execution_time"
INPUT_SHA256 = "input_sha256"
ORCHESTRATION = "orchestration"

_PATIENT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class ContextError(ValueError):
    pass


def validate_initial(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Contrôle les entrées avant toute exécution (échec rapide, message clair)."""
    pid = str(ctx.get(PATIENT_ID) or "")
    if not _PATIENT_ID_RE.match(pid):
        raise ContextError("patient_id invalide (1-64 caractères : lettres, chiffres, _ ou -)")
    has_vcf = bool(ctx.get(VCF_URI))
    has_fastq = bool(ctx.get(FASTQ_R1) or ctx.get(FASTQ_R1_URI))
    if not has_vcf and not has_fastq:
        raise ContextError("Fournir un VCF ou une paire de FASTQ")
    r1 = ctx.get(FASTQ_R1) or ctx.get(FASTQ_R1_URI)
    r2 = ctx.get(FASTQ_R2) or ctx.get(FASTQ_R2_URI)
    if has_fastq and not has_vcf:
        if not r2:
            raise ContextError("FASTQ R2 manquant")
        if str(r1).strip() == str(r2).strip():
            raise ContextError("FASTQ R1 et R2 doivent être distincts")
    return ctx
