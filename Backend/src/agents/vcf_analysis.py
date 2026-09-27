"""VCFAnalysisAgent — quality control, classification and statistics of germline panel variants."""

from __future__ import annotations

from typing import Any, Dict

from src.agents._io import read_artifact, write_artifact
from src.core import context as K
from src.core.agent import AgentResult, BaseAgent
from src.genomics import ClinVarRecord, QCThresholds, StoredAnnotator, Variant, analyze_panel, get_panel, to_vcf_metrics
from src.genomics.statistics import panel_statistics


class VCFAnalysisAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("VCFAnalysis", config)

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        pid = context[K.PATIENT_ID]
        data = read_artifact(context[K.ANNOTATED_VARIANTS], pid)
        meta = data["annotation"]

        variants, records = [], {}
        for entry in data["variants"]:
            v = Variant.from_dict(entry["variant"])
            variants.append(v)
            records[v.key] = ClinVarRecord.from_dict(entry["clinvar"]) if entry["clinvar"] else None

        annotator = StoredAnnotator(meta["source"], meta["version"], records)
        result = analyze_panel(variants, get_panel(), annotator, QCThresholds.from_env())
        analysis = result.to_dict()
        statistics = panel_statistics(result, data.get("file_summary"), context.get(K.ALIGNMENT_QC))
        warnings = [c["id"] for c in statistics["quality_checks"] if c["status"] == "WARN"]
        self.logger.info(
            f"Panel: {analysis['variants_in_panel']} variants, {len(analysis['confirmed'])} confirmed P/LP, "
            f"{len(analysis['to_confirm'])} to confirm, {len(analysis['vus'])} VUS; "
            f"Ti/Tv {statistics['panel']['ti_tv']}, het/hom {statistics['panel']['het_hom_ratio']}, "
            f"checks in warning: {warnings or 'none'}"
        )
        write_artifact(pid, "panel_analysis.json", analysis)
        write_artifact(pid, "vcf_statistics.json", statistics)
        return AgentResult.ok(**{
            K.PANEL_ANALYSIS: analysis,
            K.VCF_METRICS: to_vcf_metrics(analysis, pid),
            K.VCF_STATISTICS: statistics,
        })
