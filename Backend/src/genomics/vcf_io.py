"""Lecture VCF en Python pur (VCF brut ou bgzip), sans dépendance externe.

- Sites multi-alléliques éclatés : un `Variant` par allèle alternatif.
- Filtrage par régions dès la lecture (panel) : un VCF de génome complet est lu en une passe
  sans construire d'objet pour les millions de sites hors panel.
- Annotations VEP (CSQ) / SnpEff (ANN) lues via le format déclaré dans l'en-tête.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from src.genomics.variant import Variant, normalize_chrom

_INFO_HEADER = re.compile(r'##INFO=<ID=([^,]+),Number=([^,]+),.*?Description="([^"]*)"')
_SKIPPED_ALTS = {"*", "<NON_REF>", "<*>", "."}

Region = Tuple[str, int, int]  # (chr normalisé, début 1-based, fin incluse)


class VCFFormatError(ValueError):
    """Fichier VCF illisible ou mal formé."""


@dataclass
class VCFHeader:
    samples: List[str] = field(default_factory=list)
    info_number: Dict[str, str] = field(default_factory=dict)
    csq_fields: List[str] = field(default_factory=list)
    ann_fields: List[str] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)

    @property
    def has_clinvar_annotation(self) -> bool:
        return "CLNSIG" in self.info_number


def _open(path: Path):
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace")


def _parse_header_line(line: str, header: VCFHeader) -> None:
    m = _INFO_HEADER.match(line)
    if m:
        key, number, desc = m.groups()
        header.info_number[key] = number
        if key in ("CSQ", "ANN") and "Format:" in desc:
            fields = [f.strip() for f in desc.split("Format:", 1)[1].strip().strip("'").split("|")]
            if key == "CSQ":
                header.csq_fields = fields
            else:
                header.ann_fields = fields
        return
    if line.startswith("##") and "=" in line and not line.startswith("##INFO"):
        k, _, v = line[2:].partition("=")
        header.meta.setdefault(k, v.strip())


def _parse_info(raw: str) -> Dict[str, str]:
    if raw in ("", "."):
        return {}
    out: Dict[str, str] = {}
    for item in raw.split(";"):
        k, sep, v = item.partition("=")
        out[k] = v if sep else "true"
    return out


def _per_allele_info(info: Dict[str, str], header: VCFHeader, alt_index: int) -> Dict[str, str]:
    """Pour les champs Number=A / R, ne garde que la valeur de l'allèle courant."""
    out = dict(info)
    for k, v in info.items():
        number = header.info_number.get(k)
        if number not in ("A", "R"):
            continue
        parts = v.split(",")
        idx = alt_index - 1 if number == "A" else alt_index
        if 0 <= idx < len(parts):
            out[k] = parts[idx]
    return out


def _gene_from_annotations(info: Dict[str, str], header: VCFHeader, alt: str) -> Tuple[Optional[str], Optional[str]]:
    """(gène, conséquence) depuis CSQ (VEP) ou ANN (SnpEff), pour l'allèle alt."""
    for key, fields, gene_col, cons_col in (
        ("CSQ", header.csq_fields, "SYMBOL", "Consequence"),
        ("ANN", header.ann_fields, "Gene_Name", "Annotation"),
    ):
        if key not in info or not fields or gene_col not in fields:
            continue
        gi, ci = fields.index(gene_col), fields.index(cons_col) if cons_col in fields else None
        first = None
        for entry in info[key].split(","):
            parts = entry.split("|")
            if len(parts) <= gi:
                continue
            cand = (parts[gi] or None, parts[ci] if ci is not None and len(parts) > ci else None)
            if first is None:
                first = cand
            if parts[0] == alt:
                return cand
        if first:
            return first
    gene = info.get("GENE") or (info.get("GENEINFO", "").split(":")[0] or None)
    return (gene.split(",")[0].strip() if gene else None), None


def _in_regions(chrom: str, pos: int, end: int, regions: Dict[str, List[Tuple[int, int]]]) -> bool:
    for s, e in regions.get(chrom, ()):
        if pos <= e and end >= s:
            return True
    return False


def read_header(path: Path) -> VCFHeader:
    header = VCFHeader()
    with _open(Path(path)) as fh:
        for line in fh:
            if line.startswith("##"):
                _parse_header_line(line.rstrip("\n"), header)
            elif line.startswith("#CHROM"):
                header.samples = line.rstrip("\n").split("\t")[9:]
                break
            else:
                break
    return header


def iter_variants(
    path: Path,
    regions: Optional[Sequence[Region]] = None,
    sample: Optional[str] = None,
) -> Iterator[Variant]:
    """Itère les variants (un par allèle alt) ; `regions` restreint la lecture au panel."""
    path = Path(path)
    header = VCFHeader()
    region_map: Optional[Dict[str, List[Tuple[int, int]]]] = None
    if regions is not None:
        region_map = {}
        for c, s, e in regions:
            region_map.setdefault(normalize_chrom(c), []).append((s, e))

    sample_idx = 0
    seen_columns = False
    with _open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            if line.startswith("##"):
                _parse_header_line(line.rstrip("\n"), header)
                continue
            if line.startswith("#CHROM"):
                header.samples = line.rstrip("\n").split("\t")[9:]
                if sample and sample in header.samples:
                    sample_idx = header.samples.index(sample)
                seen_columns = True
                continue
            if not line.strip():
                continue
            if not seen_columns:
                raise VCFFormatError(f"{path}: ligne #CHROM absente avant les données")
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 8:
                raise VCFFormatError(f"{path}:{lineno}: {len(cols)} colonnes (8 minimum)")
            chrom = normalize_chrom(cols[0])
            try:
                pos = int(cols[1])
            except ValueError as e:
                raise VCFFormatError(f"{path}:{lineno}: POS invalide {cols[1]!r}") from e
            ref = cols[3]
            if region_map is not None and not _in_regions(chrom, pos, pos + len(ref) - 1, region_map):
                continue

            qual = None if cols[5] in (".", "") else float(cols[5])
            filt = cols[6] or "."
            info = _parse_info(cols[7])
            fmt: Dict[str, str] = {}
            if len(cols) > 9 + sample_idx and cols[8]:
                keys = cols[8].split(":")
                vals = cols[9 + sample_idx].split(":")
                fmt = {k: (vals[i] if i < len(vals) else ".") for i, k in enumerate(keys)}

            for alt_index, alt in enumerate(cols[4].split(","), 1):
                if alt in _SKIPPED_ALTS or alt.startswith("<"):
                    continue
                allele_info = _per_allele_info(info, header, alt_index)
                gene, consequence = _gene_from_annotations(allele_info, header, alt)
                yield Variant(
                    chromosome=chrom,
                    position=pos,
                    ref=ref,
                    alt=alt,
                    quality=qual,
                    filter_status=filt,
                    info=allele_info,
                    format_data=fmt,
                    alt_index=alt_index,
                    gene=gene,
                    consequence=consequence,
                    clinvar=allele_info.get("CLNSIG"),
                )


def read_variants(path: Path, regions: Optional[Sequence[Region]] = None) -> Tuple[VCFHeader, List[Variant]]:
    variants = list(iter_variants(path, regions))
    return read_header(path), variants


def trim_alleles(pos: int, ref: str, alt: str) -> Tuple[int, str, str]:
    """Représentation minimale (retire suffixe puis préfixe communs, en gardant 1 base d'ancrage).

    Permet d'apparier un allèle issu d'un site multi-allélique éclaté (ex. REF=GTT ALT=G,GT)
    avec l'entrée ClinVar (REF=GT ALT=G). Ne remplace pas une normalisation gauche complète,
    faite en amont par GATK LeftAlignAndTrimVariants dans le pipeline.
    """
    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt, pos = ref[1:], alt[1:], pos + 1
    return pos, ref, alt
