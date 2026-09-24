"""Registre des outils (agents) — source unique pour le planificateur, le MCP et l'API.

Chaque outil déclare ce qu'il consomme (`requires`) et ce qu'il produit (`produces`) dans le
contexte partagé. Le planificateur en déduit dynamiquement quels agents exécuter et dans quel
ordre : fournir un VCF saute l'alignement, demander un entraînement ajoute l'agent LoRA, etc.
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
    agent: str  # "module:Classe", importé à la demande (torch, boto3… ne sont chargés que si utiles)
    requires: Tuple[str, ...]
    produces: Tuple[str, ...]
    ui_step: str
    gpu_phase: Optional[str] = None
    exclusive: bool = True  # False : peut tourner en parallèle d'autres outils non exclusifs
    enabled: Callable[[Dict[str, Any]], bool] = _always
    cache_inputs: Tuple[str, ...] = ()  # clés dont les fichiers identifient un résultat réutilisable
    config_env: Tuple[str, ...] = ()  # variables d'environnement qui changent le résultat
    critical: bool = True  # False : un échec est journalisé sans faire échouer l'analyse
    version: str = "1"

    def build_agent(self, config: Optional[Dict[str, Any]] = None) -> BaseAgent:
        module, cls = self.agent.split(":")
        return getattr(importlib.import_module(module), cls)(config)

    def input_schema(self) -> Dict[str, Any]:
        """Schéma JSON (MCP inputSchema) déduit des dépendances."""
        props = {k: {"type": "boolean" if k == K.TRAIN_LLM else "string"} for k in self.requires}
        return {"type": "object", "properties": props, "required": list(self.requires)}


TOOLS: Tuple[ToolSpec, ...] = (
    ToolSpec(
        name="data_manager",
        label="Préparation des données",
        description="Valide les FASTQ R1/R2 et les range dans le dossier patient du serveur.",
        agent="src.agents.data_manager:DataManagerAgent",
        requires=(K.PATIENT_ID, K.FASTQ_R1, K.FASTQ_R2),
        produces=(K.FASTQ_R1_URI, K.FASTQ_R2_URI),
        ui_step="data_manager",
    ),
    ToolSpec(
        name="genomic_pipeline",
        label="Appel de variants GATK",
        description=(
            "Alignement BWA-MEM, duplicats, BQSR puis HaplotypeCaller restreint au panel, "
            "normalisation et filtres GATK. Parabricks (GPU) ou GATK4 (CPU) selon la VRAM."
        ),
        agent="src.agents.variant_calling:VariantCallingAgent",
        requires=(K.PATIENT_ID, K.FASTQ_R1_URI, K.FASTQ_R2_URI),
        produces=(K.VCF_URI, K.BAM_URI, K.PIPELINE_BACKEND),
        ui_step="parabricks",
        gpu_phase=GPU_PARABRICKS,
        cache_inputs=(K.FASTQ_R1_URI, K.FASTQ_R2_URI),
        config_env=(
            "PIPELINE_BACKEND", "PARABRICKS_IMAGE", "GATK_DOCKER_IMAGE", "GATK_BQSR",
            "GATK_MARK_DUPLICATES", "REFERENCE_GENOME", "KNOWN_SITES_VCF", "MILLS_INDELS_VCF",
            "DBSNP_VCF", "PANEL_INTERVAL_PADDING",
        ),
        version="2",
    ),
    ToolSpec(
        name="variant_annotation",
        label="Annotation ClinVar",
        description="Lit le VCF sur les régions du panel et annote chaque allèle avec ClinVar (version tracée).",
        agent="src.agents.annotation:VariantAnnotationAgent",
        requires=(K.PATIENT_ID, K.VCF_URI),
        produces=(K.ANNOTATED_VARIANTS, K.ANNOTATION),
        ui_step="variant_annotation",
        exclusive=False,
    ),
    ToolSpec(
        name="vcf_analysis",
        label="Analyse du panel sein",
        description="Contrôle qualité clinique et classification des variants du panel germinal.",
        agent="src.agents.vcf_analysis:VCFAnalysisAgent",
        requires=(K.PATIENT_ID, K.ANNOTATED_VARIANTS),
        produces=(K.PANEL_ANALYSIS, K.VCF_METRICS),
        ui_step="vcf_analysis",
        exclusive=False,
    ),
    ToolSpec(
        name="prediction",
        label="Interprétation clinique",
        description="Niveau de risque par règles déterministes ; commentaire BioGPT optionnel (non décisionnel).",
        agent="src.agents.prediction:PredictionAgent",
        requires=(K.PATIENT_ID, K.PANEL_ANALYSIS),
        produces=(K.RISK_ASSESSMENT, K.PREDICTION_RESULTS),
        ui_step="prediction",
        gpu_phase=GPU_BIOGPT,
    ),
    ToolSpec(
        name="report",
        label="Rapport clinique",
        description="Assemble et archive le rapport clinique JSON.",
        agent="src.agents.report:ReportGeneratorAgent",
        requires=(K.PATIENT_ID, K.PANEL_ANALYSIS, K.PREDICTION_RESULTS),
        produces=(K.CLINICAL_REPORT, K.REPORT_URI),
        ui_step="report",
        exclusive=False,
    ),
    ToolSpec(
        name="llm_training",
        label="Données d'entraînement LoRA",
        description="Ajoute l'exemple patient au jeu d'entraînement (et fine-tuning si configuré).",
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
