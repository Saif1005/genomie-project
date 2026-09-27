"""Hereditary risk level — explicit, versioned rules, no language model.

  HIGH          confirmed P/LP variant in a high-penetrance gene
  MODERATE      confirmed P/LP variant in a moderate-penetrance gene, or a "low penetrance" allele
  INDETERMINATE no confirmed variant but at least one variant to confirm (insufficient QC,
                unclassified loss of function) — never give false reassurance;
                or (v1.1) no reportable variant but insufficient coverage of known pathogenic
                sites (< 90 % of the panel's ClinVar P/LP sites covered at the clinical depth)
  LOW           no P/LP variant and nothing to confirm in the panel's germline genes; if a gene
                has fewer than 95 % of its P/LP sites covered, the conclusion names it explicitly

Coverage (FASTQ mode) never downgrades HIGH/MODERATE: a confirmed pathogenic variant remains valid
whatever the coverage elsewhere. In VCF mode (no BAM) coverage cannot be measured.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional

METHOD = "germlineiq-rules-v1.1"
MIN_GLOBAL_SITE_COVERAGE = 0.90   # below: analysis not conclusive (INDETERMINATE)
MIN_GENE_SITE_COVERAGE = 0.95     # below: the gene is named as "not excluded" in the conclusion


class RiskLevel(str, Enum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    INDETERMINATE = "INDETERMINATE"


CONCLUSIONS = {
    RiskLevel.HIGH: "Hereditary breast cancer risk: HIGH",
    RiskLevel.MODERATE: "Hereditary breast cancer risk: MODERATE",
    RiskLevel.LOW: "Hereditary breast cancer risk: LOW (for the genes and variants analysed)",
    RiskLevel.INDETERMINATE: "Hereditary breast cancer risk: INDETERMINATE — confirmation required",
}
CONCLUSION_LOW_COVERAGE = "Hereditary breast cancer risk: INDETERMINATE — insufficient coverage, analysis not conclusive"

LIMITATIONS = (
    "Only single-nucleotide variants and small indels are analysed: large rearrangements and copy-number "
    "variants (e.g. BRCA1 exon deletions) are not detected.",
    "Classification relies on ClinVar; a variant absent from ClinVar is only reported if it is annotated "
    "as loss of function in the VCF.",
    "Panel coverage is not measured from a VCF: the absence of a variant does not mean the absence of a "
    "mutation in a poorly covered region.",
    "Somatic genes (PIK3CA, ERBB2/HER2, MYC) are outside the scope of a germline call.",
)


@dataclass(frozen=True)
class RiskAssessment:
    level: RiskLevel
    conclusion: str
    rationale: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=lambda: list(LIMITATIONS))
    method: str = METHOD

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["level"] = self.level.value
        return d


def _describe(f: Dict) -> str:
    stars = f.get("review_stars")
    stars_txt = f", {stars}★ ClinVar" if stars is not None else ""
    return (
        f"{f['gene']} {f['chromosome']}:{f['position']} {f['mutation']}"
        f" ({f.get('clinvar_significance') or 'unclassified'}{stars_txt}, "
        f"{f.get('zygosity') or 'unknown zygosity'}, {f.get('penetrance')} penetrance)"
    )


def _coverage_limitation(coverage: Dict) -> str:
    return (
        f"Coverage measured at {coverage['sites']} known pathogenic sites (ClinVar) of the panel genes: "
        f"{coverage['covered']} covered ≥ {coverage['min_depth']}x ({coverage['fraction_covered']:.1%}). "
        "Other regions (introns, unlisted variants) are not assessed by this measure."
    )


def assess_risk(analysis: Dict, coverage: Optional[Dict] = None) -> RiskAssessment:
    """`coverage`: the "clinvar_sites_coverage" block of the alignment QC (FASTQ mode), else None."""
    confirmed: List[Dict] = analysis.get("confirmed", [])
    to_confirm: List[Dict] = analysis.get("to_confirm", [])
    rationale: List[str] = []

    high = [f for f in confirmed if f.get("penetrance") == "high"]
    other = [f for f in confirmed if f.get("penetrance") != "high"]
    for f in high + other:
        rationale.append(f"Confirmed variant: {_describe(f)}")
    for f in to_confirm:
        reason = f.get("note") or "insufficient quality control (" + ", ".join(f.get("qc_flags", [])) + ")"
        rationale.append(f"To confirm: {_describe(f)} — {reason}")

    if high:
        level = RiskLevel.HIGH
    elif other:
        level = RiskLevel.MODERATE
    elif to_confirm:
        level = RiskLevel.INDETERMINATE
    else:
        level = RiskLevel.LOW
        rationale.append(
            f"No pathogenic or likely pathogenic variant in {len(analysis.get('panel_genes', []))} "
            f"germline genes ({analysis.get('variants_in_panel', 0)} variants called in the panel)."
        )

    conclusion = CONCLUSIONS[level]
    limitations = list(LIMITATIONS)
    frac = (coverage or {}).get("fraction_covered")
    if frac is not None:
        limitations[2] = _coverage_limitation(coverage)
        weak = [g for g in coverage.get("per_gene", []) if g.get("fraction_covered") is not None and g["fraction_covered"] < MIN_GENE_SITE_COVERAGE]
        if level is RiskLevel.LOW and frac < MIN_GLOBAL_SITE_COVERAGE:
            level, conclusion = RiskLevel.INDETERMINATE, CONCLUSION_LOW_COVERAGE
            rationale.append(
                f"Only {frac:.1%} of known pathogenic sites are covered ≥ {coverage['min_depth']}x "
                f"(minimum {MIN_GLOBAL_SITE_COVERAGE:.0%}): the absence of a variant cannot be interpreted."
            )
        elif level is RiskLevel.LOW and weak:
            names = ", ".join(f"{g['gene']} ({g['fraction_covered']:.1%})" for g in weak)
            conclusion = f"{CONCLUSIONS[RiskLevel.LOW]} — incomplete coverage, not excluded for: {', '.join(g['gene'] for g in weak)}"
            rationale.append(
                f"Coverage of known pathogenic sites < {MIN_GENE_SITE_COVERAGE:.0%} for {names}: "
                "an uncovered known mutation cannot be excluded (complete with Sanger or targeted re-sequencing)."
            )
    if analysis.get("vus"):
        rationale.append(f"{len(analysis['vus'])} variant(s) of uncertain significance (VUS), not used for risk.")
    if analysis.get("conflicting"):
        rationale.append(f"{len(analysis['conflicting'])} variant(s) with conflicting ClinVar classifications, to review.")
    rationale.append(
        f"Annotation: {analysis.get('annotation_source')} (version {analysis.get('annotation_version')}), "
        f"panel {analysis.get('panel_version')}, method {METHOD}."
    )
    return RiskAssessment(level=level, conclusion=conclusion, rationale=rationale, limitations=limitations)
