"""Santé du service et introspection des agents (registre d'outils, plan)."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter

from config.settings import biogpt, paths
from src import __version__
from src.genomics import get_panel
from src.genomics.clinvar import default_clinvar_path
from src.orchestration.registry import TOOLS, list_tools
from src.orchestration.router import build_router
from src.utils.gpu_manager import select_pipeline_backend

router = APIRouter(tags=["système"])


@router.get("/health")
def health() -> Dict[str, Any]:
    backend = select_pipeline_backend()
    panel = get_panel()
    clinvar = default_clinvar_path()
    return {
        "status": "ok",
        "service": "zaynb-genomic-backend",
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
    """Agents disponibles, leurs entrées/sorties (même description que le serveur MCP)."""
    described = {t["name"]: t for t in list_tools()}
    return [
        {**described[t.name], "label": t.label, "requires": list(t.requires), "produces": list(t.produces),
         "gpu": t.gpu_phase, "critical": t.critical}
        for t in TOOLS
    ]
