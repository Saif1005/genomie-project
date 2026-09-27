"""Alignment quality control (FASTQ mode): mapping rate, duplicates, clinical coverage.

Coverage is measured where it matters clinically: at every known pathogenic or likely pathogenic
ClinVar site in the panel's germline genes. A site covered below the clinical threshold cannot be
excluded; the report flags it (the risk rules take it into account, see src.genomics.risk).

The `parse_*` and `site_coverage` functions are pure (testable without samtools).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from loguru import logger

from src.genomics.clinvar import ClinVarIndex
from src.genomics.panel import GenePanel

ALIGNMENT_QC_VERSION = "germlineiq-alnqc-v1"
MAX_SITE_REF_LENGTH = 50  # large rearrangements excluded: not assessable by point depth

Site = Tuple[str, str, int, int, str]  # (gene, chromosome, 1-based start, inclusive end, ClinVar key)

_FLAGSTAT_RE = re.compile(r"^(\d+) \+ (\d+) (.+?)(?: \(|$)")


def parse_flagstat(text: str) -> Dict[str, Optional[float]]:
    """Text output of `samtools flagstat` → counts (QC-passed) and rates."""
    counts: Dict[str, int] = {}
    for line in text.splitlines():
        m = _FLAGSTAT_RE.match(line.strip())
        if m:
            label = m.group(3).strip()
            counts.setdefault(label, int(m.group(1)))  # first occurrence ("mapped" ≠ "primary mapped")
    total = counts.get("in total (QC-passed reads + QC-failed reads)") or counts.get("in total")
    primary = counts.get("primary")
    mapped = counts.get("mapped")
    paired = counts.get("paired in sequencing")
    proper = counts.get("properly paired")
    return {
        "total_reads": total,
        "primary_reads": primary,
        "mapped_reads": mapped,
        "mapped_rate": round(mapped / total, 4) if total and mapped is not None else None,
        "properly_paired_rate": round(proper / paired, 4) if paired and proper is not None else None,
        "supplementary_reads": counts.get("supplementary"),
        "singletons": counts.get("singletons"),
        "duplicates_flagged": counts.get("duplicates"),
    }


def parse_duplicate_metrics(text: str) -> Dict[str, Optional[float]]:
    """GATK/Picard MarkDuplicates metrics file → duplication rate."""
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    for i, line in enumerate(lines):
        if line.startswith("LIBRARY") and i + 1 < len(lines):
            header = line.split("\t")
            values = lines[i + 1].split("\t")
            row = dict(zip(header, values))
            try:
                return {
                    "duplication_rate": round(float(row["PERCENT_DUPLICATION"]), 4),
                    "read_pairs_examined": int(row["READ_PAIRS_EXAMINED"]),
                    "read_pair_duplicates": int(row["READ_PAIR_DUPLICATES"]),
                    "estimated_library_size": int(row["ESTIMATED_LIBRARY_SIZE"]) if row.get("ESTIMATED_LIBRARY_SIZE") else None,
                }
            except (KeyError, ValueError):
                break
    return {"duplication_rate": None}


def pathogenic_sites(index: ClinVarIndex, panel: GenePanel) -> List[Site]:
    """ClinVar P/LP sites located in the panel germline genes (deterministic order)."""
    genes = panel.germline_breast_genes()
    sites = []
    for key, rec in index.items():
        if not rec.is_pathogenic:
            continue
        chrom, pos, ref, _alt = key.split(":")
        start = int(pos)
        if len(ref) > MAX_SITE_REF_LENGTH:
            continue
        gene = panel.gene_at(chrom, start, genes)
        if gene is None:
            continue
        sites.append((gene.symbol, chrom, start, start + len(ref) - 1, key))
    return sorted(set(sites), key=lambda s: (s[1], s[2], s[4]))


def write_sites_bed(sites: Sequence[Site], path: Path) -> Path:
    """BED (0-based, exclusive end) merged by position: one line per unique interval."""
    intervals = sorted({(c, s - 1, e) for _g, c, s, e, _k in sites})
    path.write_text("".join(f"{c}\t{s}\t{e}\n" for c, s, e in intervals))
    return path


def parse_depth(text: str) -> Dict[Tuple[str, int], int]:
    out: Dict[Tuple[str, int], int] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            out[(parts[0], int(parts[1]))] = int(parts[2])
    return out


def _median(xs: List[int]) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return float(xs[n // 2]) if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def site_coverage(sites: Iterable[Site], depth: Dict[Tuple[str, int], int], min_depth: int, genes: Sequence[str]) -> Dict:
    """Site depth = minimum over its reference bases (an indel requires all of its bases)."""
    per_gene: Dict[str, List[int]] = {g: [] for g in genes}
    for gene, chrom, start, end, _key in sites:
        d = min(depth.get((chrom, p), 0) for p in range(start, end + 1))
        per_gene.setdefault(gene, []).append(d)
    table = []
    all_depths: List[int] = []
    for g in genes:
        ds = per_gene.get(g, [])
        all_depths += ds
        covered = sum(1 for d in ds if d >= min_depth)
        table.append({
            "gene": g,
            "sites": len(ds),
            "covered": covered,
            "fraction_covered": round(covered / len(ds), 4) if ds else None,
            "median_depth": _median(ds),
            "zero_depth_sites": sum(1 for d in ds if d == 0),
        })
    covered_all = sum(1 for d in all_depths if d >= min_depth)
    return {
        "min_depth": min_depth,
        "sites": len(all_depths),
        "covered": covered_all,
        "fraction_covered": round(covered_all / len(all_depths), 4) if all_depths else None,
        "median_depth": _median(all_depths),
        "per_gene": table,
        "definition": (
            "ClinVar pathogenic or likely pathogenic sites (≤ 50 bp) of the panel germline genes; "
            "depth with MAPQ ≥ 20 and base quality ≥ 20, minimum over the site's bases."
        ),
    }


def _run(args: List[str], timeout: int = 3600) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1]} failed: {p.stderr[-500:]}")
    return p.stdout


def compute_alignment_qc(
    bam: Path,
    duplicate_metrics: Optional[Path],
    index: Optional[ClinVarIndex],
    panel: GenePanel,
    min_depth: int,
    work_dir: Path,
    threads: int = 4,
) -> Dict:
    """Alignment metrics; each block is independent (one failure does not prevent the others)."""
    qc: Dict = {"version": ALIGNMENT_QC_VERSION, "bam": str(bam)}
    try:
        qc.update(parse_flagstat(_run(["samtools", "flagstat", "-@", str(threads), str(bam)])))
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        logger.warning(f"flagstat unavailable: {e}")
    if duplicate_metrics and Path(duplicate_metrics).is_file():
        qc.update(parse_duplicate_metrics(Path(duplicate_metrics).read_text()))
    else:
        qc["duplication_rate"] = None
    if index is not None:
        try:
            sites = pathogenic_sites(index, panel)
            bed = write_sites_bed(sites, work_dir / "clinvar_plp_sites.bed")
            depth = parse_depth(_run(["samtools", "depth", "-a", "-Q", "20", "-q", "20", "-b", str(bed), str(bam)]))
            genes = [g.symbol for g in panel.germline_breast_genes()]
            qc["clinvar_sites_coverage"] = site_coverage(sites, depth, min_depth, genes)
            qc["clinvar_version"] = index.version
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            logger.warning(f"ClinVar site coverage unavailable: {e}")
    return qc
