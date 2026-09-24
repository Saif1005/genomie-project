"""Référence hg38 et sites connus BQSR (produits par scripts/download_reference.sh)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from config.settings import gatk
from src.pipeline.executor import PipelineError

BWA_INDEX_EXT = ("amb", "ann", "bwt", "pac", "sa")


@dataclass(frozen=True)
class ReferenceBundle:
    fasta: str
    known_sites: Tuple[str, ...]


def resolve_reference(need_bwa_index: bool) -> ReferenceBundle:
    cfg = gatk()
    fasta = cfg.reference_fasta
    hint = "Exécutez : bash scripts/download_reference.sh"
    if not fasta.is_file():
        raise PipelineError(f"Référence introuvable : {fasta}. {hint}")
    for companion in (Path(f"{fasta}.fai"), fasta.with_suffix(".dict")):
        if not companion.is_file():
            raise PipelineError(f"Index de référence manquant : {companion}. {hint}")
    if need_bwa_index:
        missing = [e for e in BWA_INDEX_EXT if not Path(f"{fasta}.{e}").is_file()]
        if missing:
            raise PipelineError(f"Index BWA manquant ({', '.join(missing)}) pour {fasta}. {hint}")
    known = tuple(str(p) for p in cfg.known_sites if cfg.bqsr and p.is_file())
    return ReferenceBundle(fasta=str(fasta), known_sites=known)
