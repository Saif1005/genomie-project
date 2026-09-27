"""Deterministic ClinVar annotation.

Two sources, in order of priority:
  1. ClinVarIndex: local ClinVar GRCh38 VCF (scripts/download_reference.sh), indexed once over
     the panel regions then cached (JSON). Tracked version (fileDate).
  2. EmbeddedClinVar: INFO/CLNSIG field already present in the patient VCF (unknown version).
With neither, the analysis is impossible: we refuse to conclude (AnnotationUnavailable) rather
than return a false "low risk".
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

from loguru import logger

from src.genomics.panel import GenePanel
from src.genomics.variant import Variant, normalize_chrom
from src.genomics.vcf_io import trim_alleles

INDEX_FORMAT_VERSION = 1


class AnnotationUnavailable(RuntimeError):
    """Neither a local ClinVar release nor CLNSIG annotation in the VCF."""


class ClinicalSignificance(str, Enum):
    PATHOGENIC = "Pathogenic"
    LIKELY_PATHOGENIC = "Likely_pathogenic"
    VUS = "Uncertain_significance"
    CONFLICTING = "Conflicting_classifications"
    LIKELY_BENIGN = "Likely_benign"
    BENIGN = "Benign"
    RISK_ALLELE = "Risk_allele"
    OTHER = "Other"


PATHOGENIC_CLASSES = (ClinicalSignificance.PATHOGENIC, ClinicalSignificance.LIKELY_PATHOGENIC)

# ClinVar review stars from CLNREVSTAT
_REVIEW_STARS = {
    "practice_guideline": 4,
    "reviewed_by_expert_panel": 3,
    "criteria_provided,_multiple_submitters,_no_conflicts": 2,
    "criteria_provided,_single_submitter": 1,
    "criteria_provided,_conflicting_classifications": 1,
    "criteria_provided,_conflicting_interpretations": 1,
}


def parse_clnsig(raw: Optional[str]) -> Tuple[ClinicalSignificance, bool]:
    """Raw CLNSIG → (class, low penetrance). The order of the tests matters (substrings)."""
    s = (raw or "").strip().lower().replace(" ", "_")
    primary = s.split("|")[0]
    low_penetrance = "low_penetrance" in primary
    if not primary:
        return ClinicalSignificance.OTHER, False
    if "conflicting" in primary:
        return ClinicalSignificance.CONFLICTING, False
    if "benign" in primary:
        return (
            ClinicalSignificance.BENIGN
            if primary.startswith("benign")
            else ClinicalSignificance.LIKELY_BENIGN
        ), False
    if "pathogenic" in primary:
        if primary.startswith("likely_pathogenic"):
            return ClinicalSignificance.LIKELY_PATHOGENIC, low_penetrance
        return ClinicalSignificance.PATHOGENIC, low_penetrance
    if "uncertain" in primary:
        return ClinicalSignificance.VUS, False
    if "risk" in primary:
        return ClinicalSignificance.RISK_ALLELE, False
    return ClinicalSignificance.OTHER, False


def review_stars(revstat: Optional[str]) -> int:
    return _REVIEW_STARS.get((revstat or "").strip().lower(), 0)


@dataclass(frozen=True)
class ClinVarRecord:
    significance: ClinicalSignificance
    clnsig_raw: str
    low_penetrance: bool = False
    review_status: Optional[str] = None
    stars: int = 0
    gene: Optional[str] = None
    variation_id: Optional[str] = None
    rsid: Optional[str] = None
    hgvs: Optional[str] = None
    conditions: Optional[str] = None
    source: str = "clinvar"

    @property
    def is_pathogenic(self) -> bool:
        return self.significance in PATHOGENIC_CLASSES

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["significance"] = self.significance.value
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "ClinVarRecord":
        d = dict(d)
        d["significance"] = ClinicalSignificance(d["significance"])
        return cls(**d)


def record_from_info(info: Dict[str, str], variation_id: Optional[str] = None, source: str = "clinvar") -> Optional[ClinVarRecord]:
    raw = info.get("CLNSIG")
    if not raw:
        return None
    sig, low_pen = parse_clnsig(raw)
    geneinfo = info.get("GENEINFO", "")
    rs = info.get("RS")
    return ClinVarRecord(
        significance=sig,
        clnsig_raw=raw,
        low_penetrance=low_pen,
        review_status=info.get("CLNREVSTAT"),
        stars=review_stars(info.get("CLNREVSTAT")),
        gene=geneinfo.split(":")[0] or None if geneinfo else None,
        variation_id=variation_id,
        rsid=f"rs{rs}" if rs and not rs.startswith("rs") else rs,
        hgvs=info.get("CLNHGVS"),
        conditions=(info.get("CLNDN") or "").replace("_", " ") or None,
        source=source,
    )


def _lookup_keys(v: Variant) -> Tuple[Tuple[str, int, str, str], ...]:
    trimmed = trim_alleles(v.position, v.ref, v.alt)
    keys = [(v.chromosome, v.position, v.ref, v.alt)]
    if trimmed != (v.position, v.ref, v.alt):
        keys.append((v.chromosome, *trimmed))
    return tuple(keys)


class VariantAnnotator(Protocol):
    name: str
    version: str

    def annotate(self, variant: Variant) -> Optional[ClinVarRecord]: ...


class EmbeddedClinVar:
    """Uses INFO/CLNSIG from the patient VCF (upstream annotation, unknown version)."""

    name = "vcf-embedded-clinvar"
    version = "unknown"

    def annotate(self, variant: Variant) -> Optional[ClinVarRecord]:
        return record_from_info(variant.info, source=self.name)


class ClinVarIndex:
    """ClinVar subset restricted to the panel regions, indexed by (chr, pos, ref, alt)."""

    name = "clinvar"

    def __init__(self, records: Dict[str, Dict], version: str, source: Path):
        self._records = records
        self.version = version
        self.source = source

    def __len__(self) -> int:
        return len(self._records)

    def items(self):
        """("chr:pos:ref:alt" key, record) sorted by key: deterministic iteration."""
        for k in sorted(self._records):
            yield k, ClinVarRecord.from_dict(self._records[k])

    @staticmethod
    def _key(chrom: str, pos: int, ref: str, alt: str) -> str:
        return f"{chrom}:{pos}:{ref}:{alt}"

    def annotate(self, variant: Variant) -> Optional[ClinVarRecord]:
        for k in _lookup_keys(variant):
            rec = self._records.get(self._key(*k))
            if rec:
                return ClinVarRecord.from_dict(rec)
        return None

    @classmethod
    def load(cls, clinvar_vcf: Path, panel: GenePanel, padding: int = 1000) -> "ClinVarIndex":
        clinvar_vcf = Path(clinvar_vcf)
        st = clinvar_vcf.stat()
        cache = clinvar_vcf.with_name(f"{clinvar_vcf.name}.panel-{panel.version}.json")
        fingerprint = {
            "format": INDEX_FORMAT_VERSION,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "panel": panel.sha256,
            "padding": padding,
        }
        if cache.is_file():
            try:
                data = json.loads(cache.read_text())
                if data.get("fingerprint") == fingerprint:
                    return cls(data["records"], data["version"], clinvar_vcf)
            except (json.JSONDecodeError, KeyError):
                pass
        index = cls._build(clinvar_vcf, panel, padding)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"fingerprint": fingerprint, "version": index.version, "records": index._records},
            sort_keys=True,
        ))
        tmp.replace(cache)
        return index

    @classmethod
    def _build(cls, clinvar_vcf: Path, panel: GenePanel, padding: int) -> "ClinVarIndex":
        logger.info(f"Indexing ClinVar (panel {panel.version}): {clinvar_vcf} — once")
        regions: Dict[str, list] = {}
        for c, s, e, _ in panel.intervals(padding=padding):
            regions.setdefault(c, []).append((s, e))
        symbols = {g.symbol for g in panel.breast_genes()}
        version = "unknown"
        records: Dict[str, Dict] = {}
        opener = gzip.open if clinvar_vcf.suffix == ".gz" else open
        with opener(clinvar_vcf, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    if line.startswith("##fileDate="):
                        version = line.strip().split("=", 1)[1]
                    continue
                cols = line.split("\t", 8)
                chrom = normalize_chrom(cols[0])
                pos = int(cols[1])
                spans = regions.get(chrom)
                in_region = spans and any(s <= pos <= e for s, e in spans)
                if not in_region and not any(f"GENEINFO={s}:" in cols[7] or f"|{s}:" in cols[7] for s in symbols):
                    continue
                info = {}
                for item in cols[7].rstrip("\n").split(";"):
                    k, _, v = item.partition("=")
                    info[k] = v
                rec = record_from_info(info, variation_id=cols[2])
                if rec is None:
                    continue
                for alt in cols[4].split(","):
                    if alt in (".", "*"):
                        continue
                    records[cls._key(chrom, pos, cols[3].upper(), alt.upper())] = rec.to_dict()
        logger.info(f"ClinVar {version}: {len(records)} variants indexed on the panel")
        return cls(records, version, clinvar_vcf)


def default_clinvar_path() -> Path:
    from config.settings import paths

    return paths().clinvar_vcf


def build_annotator(vcf_has_clnsig: bool, panel: GenePanel, clinvar_vcf: Optional[Path] = None) -> VariantAnnotator:
    """Local ClinVar release if present, else the VCF CLNSIG, else an explicit error."""
    path = Path(clinvar_vcf) if clinvar_vcf else default_clinvar_path()
    if path.is_file():
        return ClinVarIndex.load(path, panel)
    if vcf_has_clnsig:
        logger.warning(
            f"ClinVar release missing ({path}) — using INFO/CLNSIG from the VCF (untracked version)"
        )
        return EmbeddedClinVar()
    raise AnnotationUnavailable(
        f"Unannotated VCF (no INFO/CLNSIG) and ClinVar release missing ({path}). "
        "Cannot conclude without annotation: run "
        "`bash scripts/download_reference.sh --clinvar-only` and relaunch the analysis."
    )


class StoredAnnotator:
    """Already computed annotations (annotation agent artifact), re-read identically."""

    def __init__(self, name: str, version: str, records: Dict[Tuple[str, int, str, str], Optional[ClinVarRecord]]):
        self.name = name
        self.version = version
        self._records = records

    def annotate(self, variant: Variant) -> Optional[ClinVarRecord]:
        return self._records.get(variant.key)
