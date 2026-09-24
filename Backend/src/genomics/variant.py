"""Modèle de variant (un allèle alternatif par objet — les sites multi-alléliques sont éclatés)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_CHROM_ORDER = {str(i): i for i in range(1, 23)} | {"X": 23, "Y": 24, "M": 25, "MT": 25}

# Champs INFO de fréquence en population (jamais INFO/AF de GATK : fréquence dans l'échantillon)
POPULATION_AF_KEYS = ("gnomAD_AF", "gnomADg_AF", "gnomADe_AF", "gnomad_AF", "AF_popmax", "MAX_AF")


def normalize_chrom(chrom: Any) -> str:
    """'17', 'chr17', 'MT', 'chrM' → 'chr17', 'chrM'."""
    c = str(chrom).strip()
    bare = c[3:] if c.lower().startswith("chr") else c
    if bare.upper() in ("M", "MT"):
        return "chrM"
    return f"chr{bare.upper() if bare.lower() in ('x', 'y') else bare}"


def chrom_sort_key(chrom: str) -> Tuple[int, str]:
    bare = normalize_chrom(chrom)[3:]
    return (_CHROM_ORDER.get(bare, 99), bare)


def _to_float(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "", ".") else float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    f = _to_float(v)
    return None if f is None else int(f)


@dataclass
class Variant:
    chromosome: str
    position: int
    ref: str
    alt: str
    quality: Optional[float] = None
    filter_status: str = "."
    info: Dict[str, Any] = field(default_factory=dict)
    format_data: Dict[str, Any] = field(default_factory=dict)
    alt_index: int = 1
    gene: Optional[str] = None
    consequence: Optional[str] = None
    clinvar: Optional[str] = None  # CLNSIG brut, s'il est présent

    def __post_init__(self) -> None:
        self.chromosome = normalize_chrom(self.chromosome)
        self.ref = self.ref.upper()
        self.alt = self.alt.upper()

    # --- Identité -------------------------------------------------------------
    @property
    def key(self) -> Tuple[str, int, str, str]:
        return (self.chromosome, self.position, self.ref, self.alt)

    @property
    def hgvs_g(self) -> str:
        return f"{self.chromosome}:g.{self.position}{self.ref}>{self.alt}"

    @property
    def variant_type(self) -> str:
        if len(self.ref) == 1 and len(self.alt) == 1:
            return "SNV"
        if len(self.ref) > len(self.alt):
            return "Deletion"
        if len(self.alt) > len(self.ref):
            return "Insertion"
        return "Complex"

    # --- Génotype et métriques (FORMAT de l'échantillon) ----------------------
    @property
    def genotype(self) -> Optional[str]:
        gt = self.format_data.get("GT")
        return None if gt in (None, "", ".") else str(gt)

    def _gt_alleles(self) -> List[Optional[int]]:
        gt = self.genotype
        if not gt:
            return []
        out: List[Optional[int]] = []
        for a in gt.replace("|", "/").split("/"):
            out.append(None if a == "." else int(a))
        return out

    @property
    def is_called(self) -> bool:
        """L'échantillon porte-t-il cet allèle ? (vrai si GT absent : VCF sans génotype)."""
        alleles = self._gt_alleles()
        if not alleles:
            return True
        return self.alt_index in alleles

    @property
    def zygosity(self) -> Optional[str]:
        alleles = [a for a in self._gt_alleles() if a is not None]
        if not alleles:
            return None
        n = alleles.count(self.alt_index)
        if n == 0:
            return "absent"
        if n == len(alleles):
            return "homozygous" if len(alleles) > 1 else "hemizygous"
        return "heterozygous"

    def _allele_depths(self) -> List[int]:
        ad = self.format_data.get("AD")
        if ad in (None, "", "."):
            return []
        values = ad if isinstance(ad, (list, tuple)) else str(ad).split(",")
        return [_to_int(v) or 0 for v in values]

    @property
    def depth(self) -> Optional[int]:
        dp = _to_int(self.format_data.get("DP"))
        if dp is not None:
            return dp
        ad = self._allele_depths()
        return sum(ad) if ad else None

    @property
    def vaf(self) -> Optional[float]:
        """Fraction allélique de CET allèle : AD[alt]/somme(AD), sinon FORMAT/AF."""
        ad = self._allele_depths()
        if len(ad) > self.alt_index and sum(ad) > 0:
            return ad[self.alt_index] / sum(ad)
        af = self.format_data.get("AF")
        if af not in (None, "", "."):
            values = af if isinstance(af, (list, tuple)) else str(af).split(",")
            idx = self.alt_index - 1
            if 0 <= idx < len(values):
                return _to_float(values[idx])
        return None

    # --- Annotations ----------------------------------------------------------
    @property
    def gnomad_af(self) -> Optional[float]:
        for k in POPULATION_AF_KEYS:
            if k in self.info:
                return _to_float(self.info[k])
        return None

    def get_population_af(self) -> Optional[float]:
        return self.gnomad_af

    @property
    def is_pathogenic(self) -> bool:
        from src.genomics.clinvar import ClinicalSignificance, parse_clnsig

        if not self.clinvar:
            return False
        sig, _ = parse_clnsig(self.clinvar)
        return sig in (ClinicalSignificance.PATHOGENIC, ClinicalSignificance.LIKELY_PATHOGENIC)

    # --- Sérialisation (transfert entre agents) --------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Variant":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
