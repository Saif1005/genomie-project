"""Contrôle qualité clinique d'un appel germinal.

Un variant pathogène n'est « confirmé » que s'il passe tous les critères ; sinon il est rapporté
comme « à confirmer » (Sanger / seconde technique) et rend la conclusion INDETERMINATE.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Dict, Tuple

from src.genomics.variant import Variant

PASS = "PASS"
LOW_CONFIDENCE = "LOW_CONFIDENCE"


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class QCThresholds:
    min_qual: float = 30.0
    min_depth: int = 15
    het_vaf_min: float = 0.25
    het_vaf_max: float = 0.75
    hom_vaf_min: float = 0.85

    @classmethod
    def from_env(cls) -> "QCThresholds":
        return cls(
            min_qual=_env_float("CLINICAL_MIN_QUAL", cls.min_qual),
            min_depth=int(_env_float("CLINICAL_MIN_DP", cls.min_depth)),
            het_vaf_min=_env_float("CLINICAL_HET_VAF_MIN", cls.het_vaf_min),
            het_vaf_max=_env_float("CLINICAL_HET_VAF_MAX", cls.het_vaf_max),
            hom_vaf_min=_env_float("CLINICAL_HOM_VAF_MIN", cls.hom_vaf_min),
        )

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass(frozen=True)
class QCResult:
    status: str
    flags: Tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == PASS


def assess(variant: Variant, t: QCThresholds) -> QCResult:
    flags = []
    if variant.filter_status not in ("PASS", "."):
        flags.append(f"FILTER:{variant.filter_status}")
    if variant.quality is None:
        flags.append("QUAL_ABSENTE")
    elif variant.quality < t.min_qual:
        flags.append(f"QUAL<{t.min_qual:g}")

    depth, vaf, zyg = variant.depth, variant.vaf, variant.zygosity
    if depth is None:
        flags.append("PROFONDEUR_ABSENTE")
    elif depth < t.min_depth:
        flags.append(f"DP<{t.min_depth}")

    if vaf is None:
        flags.append("VAF_ABSENTE")
    elif zyg == "homozygous" or zyg == "hemizygous":
        if vaf < t.hom_vaf_min:
            flags.append("VAF_INCOHERENTE_HOMOZYGOTE")
    elif not (t.het_vaf_min <= vaf <= t.het_vaf_max):
        # VAF basse : mosaïcisme ou hématopoïèse clonale (CHIP, fréquent pour TP53/CHEK2/ATM)
        flags.append("VAF_HORS_PLAGE_GERMINALE" if vaf > t.het_vaf_max else "VAF_BASSE_MOSAIQUE_OU_CHIP")

    return QCResult(status=PASS if not flags else LOW_CONFIDENCE, flags=tuple(flags))
