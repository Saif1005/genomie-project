"""Gene panel (data/cancer_genes/cancer_genes_db.json).

Each breast-panel gene has a role:
  - germline : hereditary predisposition, used for risk (high / moderate penetrance)
  - somatic  : tumour alteration (PIK3CA, ERBB2, MYC) — outside the scope of a germline call,
               never used for hereditary risk.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.genomics.variant import chrom_sort_key, normalize_chrom

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_ROOT / "data" / "cancer_genes" / "cancer_genes_db.json"

GERMLINE = "germline"
SOMATIC = "somatic"
PENETRANCE_HIGH = "high"
PENETRANCE_MODERATE = "moderate"

_REQUIRED_FIELDS = ("symbol", "name", "chromosome", "start_position", "end_position", "cancer_types")


class PanelError(ValueError):
    """Panel file missing or invalid."""


@dataclass(frozen=True)
class Gene:
    symbol: str
    name: str
    chromosome: str
    start: int
    end: int
    cancer_types: Tuple[str, ...]
    inheritance: Optional[str] = None
    breast_role: Optional[str] = None
    penetrance: Optional[str] = None
    aliases: Tuple[str, ...] = ()

    @property
    def in_breast_panel(self) -> bool:
        return self.breast_role is not None

    @property
    def is_germline_breast(self) -> bool:
        return self.breast_role == GERMLINE

    def contains(self, chromosome: str, position: int) -> bool:
        return normalize_chrom(chromosome) == self.chromosome and self.start <= position <= self.end


@dataclass(frozen=True)
class GenePanel:
    genes: Dict[str, Gene]
    source: Path
    sha256: str
    _aliases: Dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def load(cls, path: Optional[os.PathLike] = None) -> "GenePanel":
        p = Path(path or os.getenv("CANCER_GENES_DB_PATH") or DEFAULT_DB_PATH)
        if not p.is_absolute():
            p = (BACKEND_ROOT / p).resolve()
        if not p.is_file():
            raise PanelError(f"Gene panel not found: {p}")
        raw_bytes = p.read_bytes()
        try:
            raw = json.loads(raw_bytes)
        except json.JSONDecodeError as e:
            raise PanelError(f"Invalid panel ({p}): {e}") from e
        if not isinstance(raw, dict) or not raw:
            raise PanelError(f"Empty or malformed panel: {p}")

        genes: Dict[str, Gene] = {}
        aliases: Dict[str, str] = {}
        for key, info in raw.items():
            missing = [f for f in _REQUIRED_FIELDS if f not in info]
            if missing:
                raise PanelError(f"Gene {key}: missing fields {missing}")
            breast = info.get("breast_panel") or {}
            gene = Gene(
                symbol=str(info["symbol"]).upper(),
                name=str(info["name"]),
                chromosome=normalize_chrom(info["chromosome"]),
                start=int(info["start_position"]),
                end=int(info["end_position"]),
                cancer_types=tuple(info["cancer_types"]),
                inheritance=info.get("inheritance"),
                breast_role=breast.get("role"),
                penetrance=breast.get("penetrance"),
                aliases=tuple(a.upper() for a in info.get("aliases", [])),
            )
            if gene.start > gene.end:
                raise PanelError(f"Gene {gene.symbol}: start > end")
            if gene.is_germline_breast and gene.penetrance not in (PENETRANCE_HIGH, PENETRANCE_MODERATE):
                raise PanelError(f"Gene {gene.symbol}: high|moderate penetrance required")
            genes[gene.symbol] = gene
            aliases[key.upper()] = gene.symbol
            for a in gene.aliases:
                aliases[a] = gene.symbol
        return cls(
            genes=genes,
            source=p,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            _aliases=aliases,
        )

    @property
    def version(self) -> str:
        return self.sha256[:12]

    def get(self, symbol: Optional[str]) -> Optional[Gene]:
        if not symbol:
            return None
        s = symbol.upper()
        return self.genes.get(s) or self.genes.get(self._aliases.get(s, ""))

    def is_cancer_gene(self, symbol: Optional[str]) -> bool:
        return self.get(symbol) is not None

    def get_gene_info(self, symbol: str) -> Optional[Dict]:
        """Compatibility with the former CancerGenesDB (raw dict)."""
        g = self.get(symbol)
        if g is None:
            return None
        return {
            "symbol": g.symbol,
            "name": g.name,
            "chromosome": g.chromosome,
            "start_position": g.start,
            "end_position": g.end,
            "cancer_types": list(g.cancer_types),
            "inheritance": g.inheritance,
            "penetrance": g.penetrance,
        }

    def breast_genes(self) -> List[Gene]:
        return sorted((g for g in self.genes.values() if g.in_breast_panel), key=lambda g: g.symbol)

    def germline_breast_genes(self) -> List[Gene]:
        return [g for g in self.breast_genes() if g.is_germline_breast]

    def gene_at(self, chromosome: str, position: int, genes: Optional[Iterable[Gene]] = None) -> Optional[Gene]:
        """First gene (alphabetical order) containing the position — deterministic."""
        for g in sorted(genes if genes is not None else self.genes.values(), key=lambda g: g.symbol):
            if g.contains(chromosome, position):
                return g
        return None

    def intervals(self, padding: int = 0, germline_only: bool = False) -> List[Tuple[str, int, int, str]]:
        """Sorted, 1-based inclusive intervals of the breast panel, with padding."""
        genes = self.germline_breast_genes() if germline_only else self.breast_genes()
        out = [(g.chromosome, max(1, g.start - padding), g.end + padding, g.symbol) for g in genes]
        return sorted(out, key=lambda t: (chrom_sort_key(t[0]), t[1]))

    def write_bed(self, path: os.PathLike, padding: int = 100) -> Path:
        """BED (0-based, half-open) for GATK -L / Parabricks --interval-file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{c}\t{s - 1}\t{e}\t{name}\n" for c, s, e, name in self.intervals(padding)]
        content = "".join(lines)
        if not p.exists() or p.read_text() != content:
            p.write_text(content)
        return p


@lru_cache(maxsize=4)
def _load_cached(path: str) -> GenePanel:
    return GenePanel.load(path or None)


def get_panel() -> GenePanel:
    return _load_cached(os.getenv("CANCER_GENES_DB_PATH", ""))
