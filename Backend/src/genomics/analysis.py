"""Analyse du panel sein : variants appelés × annotation ClinVar × contrôle qualité.

Fonction pure et déterministe : mêmes entrées (VCF, panel, version ClinVar, seuils) → même
résultat, trié par position génomique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from src.genomics.clinvar import ClinicalSignificance, ClinVarRecord, VariantAnnotator
from src.genomics.panel import Gene, GenePanel, PENETRANCE_HIGH
from src.genomics.qc import QCResult, QCThresholds, assess
from src.genomics.variant import Variant, chrom_sort_key

# Conséquences perte de fonction (termes Sequence Ontology VEP / SnpEff)
LOF_CONSEQUENCES = (
    "frameshift",
    "stop_gained",
    "splice_acceptor",
    "splice_donor",
    "start_lost",
    "transcript_ablation",
)


@dataclass
class Finding:
    variant: Variant
    gene: Gene
    clinvar: Optional[ClinVarRecord]
    qc: QCResult
    note: Optional[str] = None

    @property
    def is_high_penetrance(self) -> bool:
        return self.gene.penetrance == PENETRANCE_HIGH and not (self.clinvar and self.clinvar.low_penetrance)

    def to_dict(self) -> Dict:
        v, c = self.variant, self.clinvar
        return {
            "gene": self.gene.symbol,
            "chromosome": v.chromosome,
            "position": v.position,
            "ref": v.ref,
            "alt": v.alt,
            "mutation": f"{v.ref}>{v.alt}",
            "variant_type": v.variant_type,
            "consequence": v.consequence,
            "genotype": v.genotype,
            "zygosity": v.zygosity,
            "quality": v.quality,
            "dp": v.depth,
            "vaf": round(v.vaf, 4) if v.vaf is not None else None,
            "filter": v.filter_status,
            "qc_status": self.qc.status,
            "qc_flags": list(self.qc.flags),
            "penetrance": "low" if (c and c.low_penetrance) else self.gene.penetrance,
            "inheritance": self.gene.inheritance,
            "clinvar_significance": c.significance.value if c else None,
            "clinvar_raw": c.clnsig_raw if c else None,
            "review_status": c.review_status if c else None,
            "review_stars": c.stars if c else None,
            "variation_id": c.variation_id if c else None,
            "rsid": c.rsid if c else None,
            "hgvs": c.hgvs if c else None,
            "conditions": c.conditions if c else None,
            "annotation_source": c.source if c else None,
            "is_pathogenic": bool(c and c.is_pathogenic),
            "note": self.note,
            "variant": v.to_dict(),
        }


def _sorted(findings: Iterable[Finding]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda f: (chrom_sort_key(f.variant.chromosome), f.variant.position, f.variant.ref, f.variant.alt),
    )


@dataclass
class PanelAnalysis:
    panel_genes: List[str]
    panel_version: str
    annotation_source: str
    annotation_version: str
    thresholds: QCThresholds
    variants_in_panel: int = 0
    confirmed: List[Finding] = field(default_factory=list)
    to_confirm: List[Finding] = field(default_factory=list)
    vus: List[Finding] = field(default_factory=list)
    conflicting: List[Finding] = field(default_factory=list)
    somatic_genes_skipped: int = 0

    @property
    def identified_genes(self) -> List[str]:
        return sorted({f.gene.symbol for f in self.confirmed})

    def to_dict(self) -> Dict:
        return {
            "panel_genes": self.panel_genes,
            "panel_version": self.panel_version,
            "annotation_source": self.annotation_source,
            "annotation_version": self.annotation_version,
            "qc_thresholds": self.thresholds.to_dict(),
            "variants_in_panel": self.variants_in_panel,
            "confirmed": [f.to_dict() for f in self.confirmed],
            "to_confirm": [f.to_dict() for f in self.to_confirm],
            "vus": [f.to_dict() for f in self.vus],
            "conflicting": [f.to_dict() for f in self.conflicting],
            "somatic_genes_skipped": self.somatic_genes_skipped,
            "identified_genes": self.identified_genes,
        }


def _resolve_gene(v: Variant, rec: Optional[ClinVarRecord], panel: GenePanel, breast: List[Gene]) -> Optional[Gene]:
    for symbol in (rec.gene if rec else None, v.gene):
        g = panel.get(symbol)
        if g is not None and g.in_breast_panel:
            return g
    return panel.gene_at(v.chromosome, v.position, breast)


def _is_lof(v: Variant) -> bool:
    c = (v.consequence or "").lower()
    return any(term in c for term in LOF_CONSEQUENCES)


def analyze_panel(
    variants: Iterable[Variant],
    panel: GenePanel,
    annotator: VariantAnnotator,
    thresholds: QCThresholds,
) -> PanelAnalysis:
    breast = panel.breast_genes()
    result = PanelAnalysis(
        panel_genes=[g.symbol for g in panel.germline_breast_genes()],
        panel_version=panel.version,
        annotation_source=annotator.name,
        annotation_version=annotator.version,
        thresholds=thresholds,
    )
    for v in variants:
        if not v.is_called:
            continue  # allèle non porté par l'échantillon (GT 0/0, ./.)
        rec = annotator.annotate(v)
        gene = _resolve_gene(v, rec, panel, breast)
        if gene is None:
            continue
        if not gene.is_germline_breast:
            result.somatic_genes_skipped += 1
            continue
        result.variants_in_panel += 1
        qc = assess(v, thresholds)
        sig = rec.significance if rec else None

        if rec and rec.is_pathogenic:
            (result.confirmed if qc.passed else result.to_confirm).append(Finding(v, gene, rec, qc))
        elif sig == ClinicalSignificance.CONFLICTING:
            result.conflicting.append(Finding(v, gene, rec, qc))
        elif _is_lof(v) and sig not in (ClinicalSignificance.BENIGN, ClinicalSignificance.LIKELY_BENIGN):
            # Perte de fonction non classée pathogène : à évaluer par un généticien (critère PVS1)
            result.to_confirm.append(
                Finding(v, gene, rec, qc, note="Variant perte de fonction non classé pathogène dans ClinVar")
            )
        elif sig == ClinicalSignificance.VUS:
            result.vus.append(Finding(v, gene, rec, qc))

    result.confirmed = _sorted(result.confirmed)
    result.to_confirm = _sorted(result.to_confirm)
    result.vus = _sorted(result.vus)
    result.conflicting = _sorted(result.conflicting)
    return result


def to_vcf_metrics(analysis: Dict, patient_id: str) -> Dict:
    """Format « vcf_metrics » (metadata / summary / variants) consommé par la préparation LoRA."""
    reported = analysis.get("confirmed", []) + analysis.get("to_confirm", [])
    depths = sorted(f["dp"] for f in reported if f.get("dp") is not None)
    median_dp = depths[len(depths) // 2] if depths else None
    return {
        "metadata": {
            "patient_id": patient_id,
            "coverage": median_dp,
            "coverage_definition": "profondeur médiane des variants rapportés (pas la couverture du panel)",
            "variant_count": analysis.get("variants_in_panel", 0),
            "pathogenic_count": len(analysis.get("confirmed", [])),
            "breast_cancer_detected": bool(analysis.get("confirmed")),
            "panel_version": analysis.get("panel_version"),
            "annotation_version": analysis.get("annotation_version"),
        },
        "summary": {
            "variants_in_panel": analysis.get("variants_in_panel", 0),
            "confirmed": len(analysis.get("confirmed", [])),
            "to_confirm": len(analysis.get("to_confirm", [])),
            "vus": len(analysis.get("vus", [])),
            "conflicting": len(analysis.get("conflicting", [])),
            "identified_pathogenic_genes": analysis.get("identified_genes", []),
        },
        "variants": [{k: v for k, v in f.items() if k != "variant"} for f in reported],
    }
