"""Scientific domain: gene panel, VCF reading, ClinVar annotation, QC and risk.

Pure, deterministic code (no network call, no language model): same inputs, same result.
The agents only orchestrate these functions.
"""

from src.genomics.analysis import Finding, PanelAnalysis, analyze_panel, to_vcf_metrics
from src.genomics.clinvar import (
    AnnotationUnavailable,
    ClinicalSignificance,
    ClinVarIndex,
    ClinVarRecord,
    EmbeddedClinVar,
    StoredAnnotator,
    build_annotator,
    parse_clnsig,
)
from src.genomics.panel import Gene, GenePanel, PanelError, get_panel
from src.genomics.qc import QCResult, QCThresholds
from src.genomics.risk import RiskAssessment, RiskLevel, assess_risk
from src.genomics.variant import Variant, normalize_chrom
from src.genomics.vcf_io import VCFFormatError, iter_variants, read_header

__all__ = [
    "AnnotationUnavailable",
    "ClinicalSignificance",
    "ClinVarIndex",
    "ClinVarRecord",
    "EmbeddedClinVar",
    "Finding",
    "Gene",
    "GenePanel",
    "PanelAnalysis",
    "PanelError",
    "QCResult",
    "QCThresholds",
    "RiskAssessment",
    "RiskLevel",
    "StoredAnnotator",
    "VCFFormatError",
    "Variant",
    "analyze_panel",
    "assess_risk",
    "build_annotator",
    "get_panel",
    "iter_variants",
    "normalize_chrom",
    "parse_clnsig",
    "read_header",
    "to_vcf_metrics",
]
