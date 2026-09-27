"""Keys of the context shared between agents.

The context is a JSON-serialisable dict: each agent reads its input keys and returns its output
keys. Names are centralised here so that the tool registry (requires / produces), the agents and
the API speak the same vocabulary.
"""

from __future__ import annotations

import re
from typing import Any, Dict

# User inputs
PATIENT_ID = "patient_id"
FASTQ_R1 = "fastq_r1"
FASTQ_R2 = "fastq_r2"
VCF_URI = "vcf_uri"            # provided VCF (VCF mode) or produced by variant calling
TRAIN_LLM = "train_llm"

# Agent outputs
FASTQ_R1_URI = "fastq_r1_uri"
FASTQ_R2_URI = "fastq_r2_uri"
BAM_URI = "bam_uri"
PIPELINE_BACKEND = "pipeline_backend"
ALIGNMENT_QC = "alignment_qc"
ANNOTATED_VARIANTS = "annotated_variants_path"
ANNOTATION = "annotation"
PANEL_ANALYSIS = "panel_analysis"
VCF_METRICS = "vcf_metrics"
VCF_STATISTICS = "vcf_statistics"
RISK_ASSESSMENT = "risk_assessment"
PREDICTION_RESULTS = "prediction_results"
CLINICAL_REPORT = "clinical_report"
REPORT_URI = "report_uri"
TRAINING_DATA = "training_data_path"

# Execution metadata added by the engine
STEPS_COMPLETED = "steps_completed"
EXECUTION_TIME = "execution_time"
INPUT_SHA256 = "input_sha256"
ORCHESTRATION = "orchestration"

_PATIENT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class ContextError(ValueError):
    pass


def validate_initial(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Checks the inputs before any execution (fail fast, clear message)."""
    pid = str(ctx.get(PATIENT_ID) or "")
    if not _PATIENT_ID_RE.match(pid):
        raise ContextError("invalid patient_id (1-64 characters: letters, digits, _ or -)")
    has_vcf = bool(ctx.get(VCF_URI))
    has_fastq = bool(ctx.get(FASTQ_R1) or ctx.get(FASTQ_R1_URI))
    if not has_vcf and not has_fastq:
        raise ContextError("Provide a VCF or a FASTQ pair")
    r1 = ctx.get(FASTQ_R1) or ctx.get(FASTQ_R1_URI)
    r2 = ctx.get(FASTQ_R2) or ctx.get(FASTQ_R2_URI)
    if has_fastq and not has_vcf:
        if not r2:
            raise ContextError("FASTQ R2 missing")
        if str(r1).strip() == str(r2).strip():
            raise ContextError("FASTQ R1 and R2 must be different files")
    return ctx
