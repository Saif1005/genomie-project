"""Tool (agent) registry — single source for the planner, the MCP server and the API.

Each tool declares what it consumes (`requires`) and what it produces (`produces`) in the shared
context. The planner dynamically derives which agents to run and in which order: providing a VCF
skips alignment, requesting training adds the LoRA agent, and so on.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core import context as K
from src.core.agent import BaseAgent

GPU_PARABRICKS = "parabricks"
GPU_BIOGPT = "biogpt"


def _always(_: Dict[str, Any]) -> bool:
    return True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    agent: str  # "module:Class", imported on demand (torch, boto3… only loaded when useful)
    requires: Tuple[str, ...]
    produces: Tuple[str, ...]
    ui_step: str
    gpu_phase: Optional[str] = None
    exclusive: bool = True  # False: may run in parallel with other non-exclusive tools
    enabled: Callable[[Dict[str, Any]], bool] = _always
    cache_inputs: Tuple[str, ...] = ()  # keys whose files identify a reusable result
    config_env: Tuple[str, ...] = ()  # environment variables that change the result
    critical: bool = True  # False: a failure is logged without failing the analysis
    version: str = "1"

    def build_agent(self, config: Optional[Dict[str, Any]] = None) -> BaseAgent:
        module, cls = self.agent.split(":")
        return getattr(importlib.import_module(module), cls)(config)

    def input_schema(self) -> Dict[str, Any]:
        """JSON schema (MCP inputSchema) derived from the dependencies."""
        props = {k: {"type": "boolean" if k == K.TRAIN_LLM else "string"} for k in self.requires}
        return {"type": "object", "properties": props, "required": list(self.requires)}


TOOLS: Tuple[ToolSpec, ...] = (
    ToolSpec(
        name="data_manager",
        label="Data preparation",
        description="Validates the FASTQ R1/R2 pair and stores it in the patient folder on the server.",
        agent="src.agents.data_manager:DataManagerAgent",
        requires=(K.PATIENT_ID, K.FASTQ_R1, K.FASTQ_R2),
        produces=(K.FASTQ_R1_URI, K.FASTQ_R2_URI),
        ui_step="data_manager",
    ),
    ToolSpec(
        name="genomic_pipeline",
        label="GATK variant calling",
        description=(
            "BWA-MEM alignment, duplicate marking, BQSR, then HaplotypeCaller restricted to the panel, "
            "normalisation and GATK filters; alignment QC and coverage of known pathogenic sites. "
            "Parabricks (GPU) or GATK4 (CPU) depending on VRAM."
        ),
        agent="src.agents.variant_calling:VariantCallingAgent",
        requires=(K.PATIENT_ID, K.FASTQ_R1_URI, K.FASTQ_R2_URI),
        produces=(K.VCF_URI, K.BAM_URI, K.PIPELINE_BACKEND, K.ALIGNMENT_QC),
        ui_step="parabricks",
        gpu_phase=GPU_PARABRICKS,
        cache_inputs=(K.FASTQ_R1_URI, K.FASTQ_R2_URI),
        config_env=(
            "PIPELINE_BACKEND", "PARABRICKS_IMAGE", "GATK_DOCKER_IMAGE", "GATK_BQSR",
            "GATK_MARK_DUPLICATES", "REFERENCE_GENOME", "KNOWN_SITES_VCF", "MILLS_INDELS_VCF",
            "DBSNP_VCF", "PANEL_INTERVAL_PADDING", "CLINICAL_MIN_DP",
        ),
        version="4"  # 4: Parabricks writes the duplicate metrics,
    ),
    ToolSpec(
        name="variant_annotation",
        label="ClinVar annotation",
        description="Reads the VCF over the panel regions and annotates every allele with ClinVar (tracked version).",
        agent="src.agents.annotation:VariantAnnotationAgent",
        requires=(K.PATIENT_ID, K.VCF_URI),
        produces=(K.ANNOTATED_VARIANTS, K.ANNOTATION),
        ui_step="variant_annotation",
        exclusive=False,
    ),
    ToolSpec(
        name="vcf_analysis",
        label="Breast panel analysis",
        description=(
            "Clinical quality control, classification of every panel variant and descriptive "
            "statistics (Ti/Tv, heterozygosity, QUAL/DP/GQ/VAF distributions, expert checks)."
        ),
        agent="src.agents.vcf_analysis:VCFAnalysisAgent",
        requires=(K.PATIENT_ID, K.ANNOTATED_VARIANTS),
        produces=(K.PANEL_ANALYSIS, K.VCF_METRICS, K.VCF_STATISTICS),
        ui_step="vcf_analysis",
        exclusive=False,
    ),
    ToolSpec(
        name="prediction",
        label="Clinical interpretation",
        description=(
            "Risk level from deterministic rules; verified BioGPT texts (never used for decisions): "
            "literature commentary and interpretation of the VCF statistics by the fine-tuned model."
        ),
        agent="src.agents.prediction:PredictionAgent",
        requires=(K.PATIENT_ID, K.PANEL_ANALYSIS),
        produces=(K.RISK_ASSESSMENT, K.PREDICTION_RESULTS),
        ui_step="prediction",
        gpu_phase=GPU_BIOGPT,
    ),
    ToolSpec(
        name="report",
        label="Clinical report",
        description="Assembles and archives the clinical JSON report.",
        agent="src.agents.report:ReportGeneratorAgent",
        requires=(K.PATIENT_ID, K.PANEL_ANALYSIS, K.PREDICTION_RESULTS),
        produces=(K.CLINICAL_REPORT, K.REPORT_URI),
        ui_step="report",
        exclusive=False,
    ),
    ToolSpec(
        name="llm_training",
        label="LoRA training data",
        description="Appends the patient example to the training set.",
        agent="src.agents.llm_training:LLMTrainingAgent",
        requires=(K.PATIENT_ID, K.VCF_METRICS, K.PREDICTION_RESULTS),
        produces=(K.TRAINING_DATA,),
        ui_step="llm_training",
        enabled=lambda ctx: bool(ctx.get(K.TRAIN_LLM)),
        critical=False,
    ),
)

TOOLS_BY_NAME: Dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def get_tool(name: str) -> ToolSpec:
    try:
        return TOOLS_BY_NAME[name]
    except KeyError as e:
        raise KeyError(f"Outil inconnu : {name} (disponibles : {', '.join(TOOLS_BY_NAME)})") from e


def list_tools() -> List[Dict[str, Any]]:
    """Description MCP (tools/list)."""
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.input_schema()}
        for t in TOOLS
    ]
