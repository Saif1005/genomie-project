"""Service health and agent introspection (tool registry, plan)."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter

from config.settings import biogpt, biogpt_stats, paths
from src import __version__
from src.genomics import get_panel
from src.genomics.clinvar import default_clinvar_path
from src.orchestration.registry import TOOLS, list_tools
from src.orchestration.router import build_router
from src.utils.gpu_manager import select_pipeline_backend

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> Dict[str, Any]:
    backend = select_pipeline_backend()
    panel = get_panel()
    clinvar = default_clinvar_path()
    return {
        "status": "ok",
        "service": "germlineiq-backend",
        "version": __version__,
        "deployment_mode": "local",
        "data_root": str(paths().data_root),
        "orchestrator": f"LangGraph ({build_router().name})",
        "pipeline_backend": backend["backend"],
        "pipeline_backend_reason": backend["reason"],
        "gpus": backend["gpus"],
        "panel": {"version": panel.version, "germline_genes": [g.symbol for g in panel.germline_breast_genes()]},
        "clinvar": {"path": str(clinvar), "available": clinvar.is_file()},
        "biogpt_commentary": biogpt().commentary,
    }


@router.get("/api/v1/tools")
def tools() -> List[Dict[str, Any]]:
    """Available agents and their inputs/outputs (same description as the MCP server)."""
    described = {t["name"]: t for t in list_tools()}
    return [
        {**described[t.name], "label": t.label, "requires": list(t.requires), "produces": list(t.produces),
         "gpu": t.gpu_phase, "critical": t.critical}
        for t in TOOLS
    ]


@router.get("/api/v1/models/biogpt")
def biogpt_model() -> Dict[str, Any]:
    """Commentary model in service, LoRA fine-tuning history and verification knowledge."""
    import json

    from src.llm.knowledge import CURATED_ASSOCIATIONS, DISEASE_LEXICON, KNOWLEDGE_VERSION

    cfg = biogpt()
    root = paths().models_dir / "biogpt-germlineiq-lora"

    def _read(p):
        try:
            return json.loads(p.read_text()) if p.is_file() else None
        except (OSError, json.JSONDecodeError):
            return None

    log = _read(root / "training_log.json")
    corpus = _read(paths().models_dir / "biogpt_corpus" / "pubmed_manifest.json")
    return {
        "base_model": cfg.model,
        "adapter_in_service": cfg.adapter_path,
        "commentary_enabled": cfg.commentary,
        "fine_tuning": _read(root / "history" / "summary.json"),
        "last_training": {k: log.get(k) for k in ("best_val_perplexity", "steps", "duration_s", "trainable_parameters", "total_parameters", "device", "versions", "config", "evals")} if log else None,
        "corpus": {k: corpus.get(k) for k in ("version", "retrieved_at", "abstracts_kept", "pmids_found", "per_gene", "date_range", "sha256")} if corpus else None,
        "knowledge": {
            "version": KNOWLEDGE_VERSION,
            "curated": {g: [DISEASE_LEXICON[d][0] for d in ds] for g, ds in sorted(CURATED_ASSOCIATIONS.items())},
        },
        "statistics_model": _statistics_model(_read),
    }


def _statistics_model(_read) -> Dict[str, Any]:
    """BioGPT fine-tuned on the VCF statistics (src.llm.stats_finetune): dataset, training, gate, service."""
    from src.llm.stats_interpretation import FACTS_VERSION, REFERENCE_VERSION, VERIFIER_VERSION
    from src.llm.stats_model import StatsInterpreter

    root = paths().models_dir / "biogpt-germlineiq-stats"
    log = _read(root / "candidate" / "training_log.json")
    evaluation = _read(root / "candidate" / "evaluation.json")
    dataset = _read(root / "dataset" / "dataset_manifest.json")

    def summary(res):
        return {k: v for k, v in res.items() if k != "samples"} if res else None

    return {
        "enabled": biogpt_stats().enabled,
        "adapter_path": biogpt_stats().adapter_path,
        "in_service": StatsInterpreter().available,
        "promoted": _read(root / "promoted" / "manifest.json"),
        "versions": {"facts": FACTS_VERSION, "reference": REFERENCE_VERSION, "verifier": VERIFIER_VERSION},
        "dataset": {k: dataset.get(k) for k in ("created_at", "seed", "splits")} if dataset else None,
        "training": {k: log.get(k) for k in ("best_val_loss", "steps", "duration_s", "trainable_parameters", "total_parameters", "device", "evals")} if log else None,
        "evaluation": {
            "gate_thresholds": evaluation.get("gate_thresholds"),
            "promotion": evaluation.get("promotion"),
            "base_zero_shot": {k: summary(v) for k, v in evaluation.get("base_zero_shot", {}).items()},
            "fine_tuned": {k: summary(v) for k, v in evaluation.get("fine_tuned", {}).items()},
        } if evaluation else None,
    }


@router.get("/api/v1/benchmarks/latest")
def latest_benchmark() -> Dict[str, Any]:
    """Latest multi-agent benchmark results (python -m benchmarks.multiagent)."""
    import json

    from fastapi import HTTPException

    path = paths().data_root / "benchmarks" / "latest.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No benchmark has been run yet (python -m benchmarks.multiagent)")
    return json.loads(path.read_text())
