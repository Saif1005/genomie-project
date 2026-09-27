"""hg38 reference and BQSR known sites (produced by scripts/download_reference.sh)."""

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
    hint = "Run: bash scripts/download_reference.sh"
    if not fasta.is_file():
        raise PipelineError(f"Reference not found: {fasta}. {hint}")
    for companion in (Path(f"{fasta}.fai"), fasta.with_suffix(".dict")):
        if not companion.is_file():
            raise PipelineError(f"Reference index missing: {companion}. {hint}")
    if need_bwa_index:
        missing = [e for e in BWA_INDEX_EXT if not Path(f"{fasta}.{e}").is_file()]
        if missing:
            raise PipelineError(f"BWA index missing ({', '.join(missing)}) for {fasta}. {hint}")
    known = tuple(str(p) for p in cfg.known_sites if cfg.bqsr and p.is_file())
    return ReferenceBundle(fasta=str(fasta), known_sites=known)
