"""Assemblage du rapport clinique JSON à partir du contexte du pipeline.

Aucune décision ici : le texte est dérivé de l'analyse du panel et du niveau de risque déjà
calculés, par gabarits fixes (même analyse → même texte).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src import __version__
from src.core import context as K
from src.schemas.clinical_report import (
    AnnotationInfo,
    ClinicalPrediction,
    ClinicalReport,
    GATKMetrics,
    GenomicFindings,
    PathogenicVariantFinding,
    Reproducibility,
    SystemMetrics,
)

_ENGINE_LABELS = {
    "parabricks": "NVIDIA Clara Parabricks + GATK (GPU)",
    "gatk4-cpu": "GATK4 BWA-MEM / HaplotypeCaller (CPU)",
}
_ZYGOSITY_FR = {"heterozygous": "hétérozygote", "homozygous": "homozygote", "hemizygous": "hémizygote"}


def to_finding(f: Dict[str, Any]) -> PathogenicVariantFinding:
    return PathogenicVariantFinding(
        gene=f["gene"],
        chromosome=f["chromosome"],
        position=int(f["position"]),
        mutation=f["mutation"],
        gatk_metrics=GATKMetrics(
            QUAL=round(float(f["quality"]), 1) if f.get("quality") is not None else None,
            DP=f.get("dp"),
            VAF=round(float(f["vaf"]), 3) if f.get("vaf") is not None else None,
        ),
        pathogenicity=f.get("clinvar_significance") or "Non classé",
        inheritance=f.get("inheritance"),
        zygosity=f.get("zygosity"),
        penetrance=f.get("penetrance"),
        hgvs=f.get("hgvs"),
        rsid=f.get("rsid"),
        variation_id=f.get("variation_id"),
        review_status=f.get("review_status"),
        review_stars=f.get("review_stars"),
        conditions=f.get("conditions"),
        consequence=f.get("consequence"),
        filter=f.get("filter"),
        qc_status=f.get("qc_status"),
        qc_flags=f.get("qc_flags") or [],
        note=f.get("note"),
    )


def _sentence(f: Dict[str, Any]) -> str:
    ident = f.get("hgvs") or f"{f['chromosome']}:{f['position']} {f['mutation']}"
    rs = f" ({f['rsid']})" if f.get("rsid") else ""
    zyg = _ZYGOSITY_FR.get(f.get("zygosity") or "", "de zygotie indéterminée")
    stars = f"{f['review_stars']}★" if f.get("review_stars") is not None else "revue inconnue"
    vaf = f"{f['vaf']:.2f}" if f.get("vaf") is not None else "NA"
    return (
        f"Variant {ident}{rs} {zyg} dans {f['gene']} (pénétrance {f.get('penetrance')}), classé "
        f"{f.get('clinvar_significance')} dans ClinVar ({stars}) ; QUAL={f.get('quality')}, "
        f"DP={f.get('dp')}, VAF={vaf}."
    )


def build_clinical_summary(analysis: Dict[str, Any], risk_level: str) -> str:
    confirmed = analysis.get("confirmed", [])
    to_confirm = analysis.get("to_confirm", [])
    genes = len(analysis.get("panel_genes", []))
    parts: List[str] = [_sentence(f) for f in confirmed]
    for f in to_confirm:
        why = f.get("note") or "critères de qualité non remplis (" + ", ".join(f.get("qc_flags", [])) + ")"
        parts.append(f"À confirmer par une seconde technique : {_sentence(f)} Motif : {why}.")
    if not confirmed and not to_confirm:
        parts.append(
            f"Aucun variant pathogène ou probablement pathogène (ClinVar) n'a été identifié sur les "
            f"{genes} gènes germinaux du panel ({analysis.get('variants_in_panel', 0)} variants appelés "
            "dans les régions du panel)."
        )
    if risk_level in ("HIGH", "MODERATE", "INDETERMINATE"):
        parts.append("Une consultation d'oncogénétique est recommandée pour l'interprétation et le conseil familial.")
    return " ".join(parts)


def _engine_label(ctx: Dict[str, Any]) -> str:
    backend = ctx.get(K.PIPELINE_BACKEND)
    if backend:
        return _ENGINE_LABELS.get(backend, backend)
    return "VCF fourni (appel de variants externe)"


def _hardware() -> str:
    explicit = os.getenv("PIPELINE_HARDWARE")
    if explicit:
        return explicit
    from src.utils.gpu_manager import gpu_inventory

    gpus = gpu_inventory()
    gpu_txt = ", ".join(f"{g['name']} ({g['vram_mb'] / 1024:.0f} Go)" for g in gpus) or "sans GPU"
    return f"Serveur local ({os.cpu_count()} cœurs) — {gpu_txt}"


def build_clinical_report(
    context: Dict[str, Any],
    execution_time_seconds: float = 0.0,
    steps_completed: Optional[List[str]] = None,
    orchestration: Optional[Dict[str, Any]] = None,
) -> ClinicalReport:
    pid = context.get(K.PATIENT_ID, "UNKNOWN")
    analysis: Dict[str, Any] = context.get(K.PANEL_ANALYSIS) or {}
    prediction: Dict[str, Any] = context.get(K.PREDICTION_RESULTS) or {}
    annotation: Dict[str, Any] = context.get(K.ANNOTATION) or {}
    input_sha = context.get(K.INPUT_SHA256)
    risk_level = prediction.get("risk_level", "INDETERMINATE")

    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    report_id = f"REP-{pid}-{date}" + (f"-{input_sha[:8]}" if input_sha else "")
    commentary_model = prediction.get("commentary_model")
    model = prediction.get("decision_method") or "zaynb-rules-v1"
    if commentary_model:
        model = f"{model} + commentaire {commentary_model}"

    return ClinicalReport(
        report_id=report_id,
        patient_id=pid,
        system_metrics=SystemMetrics(
            execution_time_seconds=round(execution_time_seconds, 1),
            pipeline_engine=_engine_label(context),
            hardware=_hardware(),
            steps_completed=list(steps_completed or []),
            orchestration=orchestration or {},
        ),
        genomic_findings=GenomicFindings(
            breast_cancer_panel_analyzed=analysis.get("panel_genes", []),
            pathogenic_variants_detected=[to_finding(f) for f in analysis.get("confirmed", [])],
            variants_to_confirm=[to_finding(f) for f in analysis.get("to_confirm", [])],
            vus_detected=[to_finding(f) for f in analysis.get("vus", [])],
            conflicting_variants=[to_finding(f) for f in analysis.get("conflicting", [])],
            breast_cancer_risk_detected=risk_level in ("HIGH", "MODERATE"),
            identified_pathogenic_genes=analysis.get("identified_genes", []),
            variants_in_panel=analysis.get("variants_in_panel", 0),
            annotation=AnnotationInfo(
                source=analysis.get("annotation_source"), version=analysis.get("annotation_version")
            ),
        ),
        clinical_prediction=ClinicalPrediction(
            model=model,
            risk_level=risk_level,
            diagnostic_conclusion=prediction.get("diagnostic_conclusion", ""),
            clinical_summary=build_clinical_summary(analysis, risk_level),
            rationale=prediction.get("rationale", []),
            limitations=prediction.get("limitations", []),
            decision_method=prediction.get("decision_method"),
            model_commentary=prediction.get("model_commentary"),
            commentary_model=commentary_model,
        ),
        reproducibility=Reproducibility(
            input_sha256=input_sha,
            panel_version=analysis.get("panel_version"),
            annotation_source=annotation.get("source") or analysis.get("annotation_source"),
            annotation_version=annotation.get("version") or analysis.get("annotation_version"),
            qc_thresholds=analysis.get("qc_thresholds", {}),
            decision_method=prediction.get("decision_method"),
            software_version=__version__,
        ),
        report_path=context.get(K.REPORT_URI),
    )
