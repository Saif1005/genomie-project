"""VCF Analysis Agent - Analyzes variants from VCF file."""

from typing import Dict, Any, List, Optional
from pathlib import Path

from pydantic import BaseModel, Field
from loguru import logger

from src.agents.base_agent import BaseAgent, AgentResult, AgentStatus
from src.preprocessing.vcf_parser import VCFParser, Variant
from src.report.clinical_report_builder import (
    variant_dict_to_finding,
    _panel_symbols_from_db,
)
from src.storage import get_storage
from config.deployment import is_local
from config.runtime_config import runtime_config


class BreastCancerAnalysisResult(BaseModel):
    """Sortie structurée de l'analyse panel cancer du sein."""

    breast_cancer_risk_detected: bool
    identified_pathogenic_genes: List[str] = Field(default_factory=list)


def _normalize_s3_path(s3_uri: str, default_bucket: str) -> str:
    if not s3_uri:
        return s3_uri
    if s3_uri.startswith("s3://"):
        if s3_uri.startswith("s3://bucket/"):
            key = s3_uri.replace("s3://bucket/", "")
            return f"s3://{default_bucket}/{key}"
        return s3_uri
    if not s3_uri.startswith("/"):
        return f"s3://{default_bucket}/{s3_uri}"
    return s3_uri


def _variant_to_dict(variant: Variant, vcf_parser: VCFParser) -> Dict[str, Any]:
    enriched = vcf_parser.get_enriched_variant_metrics(variant)
    variant_type = (
        "SNV"
        if len(variant.ref) == 1 and len(variant.alt) == 1
        else "Deletion"
        if len(variant.ref) > len(variant.alt)
        else "Insertion"
        if len(variant.alt) > len(variant.ref)
        else "Complex"
    )
    enriched["variant_type"] = variant_type
    enriched["dp"] = variant.depth
    return enriched


class VCFAnalysisAgent(BaseAgent):
    """Agent for analyzing VCF files and extracting breast cancer panel variants."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("VCFAnalysis", config)
        self.storage = get_storage()

    def validate_input(self, context: Dict[str, Any]) -> bool:
        if "vcf_s3" not in context:
            self.logger.error("Missing vcf_s3 in context")
            return False
        return True

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        patient_id = context.get("patient_id")
        vcf_s3 = context.get("vcf_s3")
        work_dir = Path("./work") / str(patient_id)
        if is_local():
            work_dir = runtime_config.work_mount / str(patient_id)
        else:
            from config.aws_config import aws_config

            vcf_s3 = _normalize_s3_path(vcf_s3, aws_config.s3_output_bucket)

        try:
            self.logger.info(f"Récupération VCF depuis {vcf_s3}...")
            name = Path(vcf_s3.replace("s3://", "", 1).partition("/")[2] or vcf_s3).name
            vcf_dest = work_dir / (name or "variants.vcf")
            if not str(vcf_dest).endswith((".vcf", ".vcf.gz")):
                vcf_dest = work_dir / "variants.vcf"
            vcf_local = self.storage.fetch(vcf_s3, vcf_dest)
            vcf_size = Path(vcf_local).stat().st_size
            self.logger.info(f"✓ VCF disponible: {vcf_local} ({vcf_size} bytes)")

            vcf_parser = VCFParser(
                vcf_local,
                min_quality=10.0,
                min_vaf=0.01,
                min_depth=2,
                require_pass=False,
            )
            variants = vcf_parser.parse()
            self.logger.info(f"Total variants after QUAL/DP/VAF filters: {len(variants)}")

            breast_panel = vcf_parser.get_breast_cancer_panel()
            self.logger.info(
                f"Breast panel genes: "
                f"{sorted({i['symbol'] for i in breast_panel.values()})}"
            )

            panel_variants, identified_genes = (
                vcf_parser.intersect_breast_panel_pathogenic_variants(variants)
            )
            self.logger.info(
                f"Pathogenic panel variants: {len(panel_variants)} "
                f"(parsed={len(variants)}, genes={identified_genes})"
            )
            analysis_result = BreastCancerAnalysisResult(
                breast_cancer_risk_detected=len(identified_genes) > 0,
                identified_pathogenic_genes=identified_genes,
            )
            self.logger.info(
                f"Breast cancer risk detected: {analysis_result.breast_cancer_risk_detected}, "
                f"genes: {analysis_result.identified_pathogenic_genes}"
            )

            summary = vcf_parser.get_variant_summary(variants)
            summary["breast_cancer_risk_detected"] = (
                analysis_result.breast_cancer_risk_detected
            )
            summary["identified_pathogenic_genes"] = (
                analysis_result.identified_pathogenic_genes
            )

            work_dir.mkdir(parents=True, exist_ok=True)
            metrics_json_path = str(work_dir / "vcf_metrics.json")
            vcf_metrics_payload = vcf_parser.export_metrics_json(
                variants=variants,
                coverage=None,
                patient_id=patient_id,
                output_path=metrics_json_path,
                include_all_variants=False,
            )
            vcf_metrics_payload["breast_cancer_analysis"] = (
                analysis_result.model_dump()
            )

            variants_dict = [
                _variant_to_dict(v, vcf_parser) for v in panel_variants
            ]
            clinical_variants = [
                variant_dict_to_finding(d).model_dump() for d in variants_dict
            ]
            genomic_findings = {
                "breast_cancer_panel_analyzed": _panel_symbols_from_db(),
                "pathogenic_variants_detected": clinical_variants,
                "breast_cancer_risk_detected": analysis_result.breast_cancer_risk_detected,
                "identified_pathogenic_genes": analysis_result.identified_pathogenic_genes,
            }
            breast_cancer_variants = variants_dict

            coverage = (
                sum(v.depth for v in variants if v.depth) / len(variants)
                if variants
                else 30.0
            )

            return AgentResult(
                success=True,
                status=AgentStatus.COMPLETED,
                data={
                    "vcf_local_path": vcf_local,
                    "total_variants": len(variants),
                    "pathogenic_cancer_variants": len(panel_variants),
                    "variants": variants_dict,
                    "breast_cancer_variants": breast_cancer_variants,
                    "breast_cancer_analysis": analysis_result.model_dump(),
                    "breast_cancer_risk_detected": (
                        analysis_result.breast_cancer_risk_detected
                    ),
                    "identified_pathogenic_genes": (
                        analysis_result.identified_pathogenic_genes
                    ),
                    "genomic_findings": genomic_findings,
                    "summary": summary,
                    "coverage": coverage,
                    "patient_id": patient_id,
                    "vcf_metrics_path": metrics_json_path,
                    "vcf_metrics": vcf_metrics_payload,
                },
            )

        except Exception as e:
            error_msg = f"VCF Analysis failed: {e}"
            self.logger.error(error_msg)
            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error=error_msg,
            )
