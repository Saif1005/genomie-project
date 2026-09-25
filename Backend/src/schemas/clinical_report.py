"""Schéma JSON du rapport clinique (sortie de l'API).

Rétrocompatible : les champs historiques (gatk_metrics, pathogenicity, risk_level, …) sont
conservés ; les champs ajoutés sont optionnels.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

LEGAL_DISCLAIMER = (
    "Ce rapport est généré par un système d'intelligence artificielle à des fins "
    "d'aide à la décision. Il ne constitue pas un diagnostic médical et doit être "
    "validé par un oncologue ou un généticien clinique avant toute prise de décision thérapeutique."
)


class GATKMetrics(BaseModel):
    QUAL: Optional[float] = None
    DP: Optional[int] = None
    VAF: Optional[float] = None


class PathogenicVariantFinding(BaseModel):
    gene: str
    chromosome: str
    position: int
    mutation: str
    gatk_metrics: GATKMetrics
    pathogenicity: str = "Pathogenic"  # classification ClinVar
    inheritance: Optional[str] = None
    zygosity: Optional[str] = None
    penetrance: Optional[str] = None
    hgvs: Optional[str] = None
    rsid: Optional[str] = None
    variation_id: Optional[str] = None
    review_status: Optional[str] = None
    review_stars: Optional[int] = None
    conditions: Optional[str] = None
    consequence: Optional[str] = None
    filter: Optional[str] = None
    qc_status: Optional[str] = None
    qc_flags: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class AnnotationInfo(BaseModel):
    source: Optional[str] = None
    version: Optional[str] = None


class GenomicFindings(BaseModel):
    breast_cancer_panel_analyzed: List[str] = Field(default_factory=list)
    pathogenic_variants_detected: List[PathogenicVariantFinding] = Field(default_factory=list)
    variants_to_confirm: List[PathogenicVariantFinding] = Field(default_factory=list)
    vus_detected: List[PathogenicVariantFinding] = Field(default_factory=list)
    conflicting_variants: List[PathogenicVariantFinding] = Field(default_factory=list)
    breast_cancer_risk_detected: bool = False
    identified_pathogenic_genes: List[str] = Field(default_factory=list)
    variants_in_panel: int = 0
    annotation: AnnotationInfo = Field(default_factory=AnnotationInfo)


class SystemMetrics(BaseModel):
    execution_time_seconds: float = 0.0
    pipeline_engine: str = "—"
    hardware: str = "—"
    steps_completed: List[str] = Field(default_factory=list)
    orchestration: Dict[str, Any] = Field(default_factory=dict)


class ClinicalPrediction(BaseModel):
    model: str = "zaynb-rules-v1"
    risk_level: str = "INDETERMINATE"
    diagnostic_conclusion: str = ""
    clinical_summary: str = ""
    rationale: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    decision_method: Optional[str] = None
    model_commentary: Optional[str] = None
    commentary_model: Optional[str] = None
    legal_disclaimer: str = LEGAL_DISCLAIMER
    status: str = "AWAITING_MEDICAL_VALIDATION"


class Reproducibility(BaseModel):
    """Tout ce qu'il faut pour rejouer l'analyse à l'identique."""

    input_sha256: Optional[str] = None
    panel_version: Optional[str] = None
    annotation_source: Optional[str] = None
    annotation_version: Optional[str] = None
    qc_thresholds: Dict[str, Any] = Field(default_factory=dict)
    decision_method: Optional[str] = None
    software_version: Optional[str] = None


class ClinicalReport(BaseModel):
    report_id: str
    patient_id: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    system_metrics: SystemMetrics = Field(default_factory=SystemMetrics)
    genomic_findings: GenomicFindings = Field(default_factory=GenomicFindings)
    clinical_prediction: ClinicalPrediction = Field(default_factory=ClinicalPrediction)
    reproducibility: Reproducibility = Field(default_factory=Reproducibility)
    report_path: Optional[str] = None

    def to_api_dict(self) -> dict:
        return self.model_dump(exclude_none=True)
