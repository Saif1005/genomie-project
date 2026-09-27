"""Descriptive statistics and quality control of a germline VCF.

Pure, deterministic functions:
- quantiles by linear interpolation ("type 7", the numpy/R default);
- fixed-bin histograms (comparable from one patient to another);
- fixed rounding, sorted lists: same VCF → same JSON, byte for byte.

Two scopes:
- `summarize_file`: the whole VCF (streamed, counters only);
- `panel_statistics`: germline panel variants, with ClinVar annotation and clinical QC.

The "expert" checks (`quality_checks`) compare each metric with the range expected for a human
germline sample. They never change the risk level: they flag suspicious data (contamination,
poor calling, insufficient coverage) to review.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.genomics.analysis import CATEGORIES, Finding, PanelAnalysis
from src.genomics.variant import Variant, chrom_sort_key

STATISTICS_VERSION = "germlineiq-stats-v1"

_TRANSITIONS = {("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")}

# Fixed histogram edges: [low, high); the last bin is open (≥ last edge)
QUAL_EDGES = (0, 20, 30, 50, 100, 200, 500, 1000, 5000)
DP_EDGES = (0, 10, 15, 20, 30, 50, 75, 100, 150, 250, 500)
GQ_EDGES = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 99)
VAF_EDGES = tuple(round(i * 0.05, 2) for i in range(21))  # 0.00 … 1.00, step 0.05
INDEL_LEN_EDGES = (1, 2, 3, 4, 5, 6, 11, 21, 51)

# Expected ranges (human germline sample, GATK HaplotypeCaller calls)
TITV_RANGE = (1.8, 3.3)          # ≈ 2.0-2.1 whole genome; ≈ 2.8-3.0 coding exome
HET_HOM_RANGE = (1.0, 3.0)       # ≈ 1.4-2.2 depending on ancestry
HET_VAF_MEDIAN_RANGE = (0.40, 0.60)
MIN_PASS_RATE = 0.80
MIN_SNV_FOR_RATIOS = 30
MIN_HET_FOR_VAF = 10
MIN_SITE_COVERAGE_FRACTION = 0.95  # ClinVar P/LP sites covered ≥ clinical depth threshold

STATUS_OK, STATUS_WARN, STATUS_NA = "OK", "WARN", "NA"


# --- Primitives -----------------------------------------------------------------
def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """Quantile by linear interpolation (type 7) over already sorted values."""
    n = len(sorted_values)
    if n == 0:
        return None
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def describe(values: Iterable[Optional[float]], nd: int = 4) -> Dict[str, Any]:
    """n, min, Q1, median, Q3, max, mean, standard deviation (sample, n-1)."""
    xs = sorted(float(v) for v in values if v is not None and not math.isnan(float(v)))
    n = len(xs)
    if n == 0:
        return {"n": 0, "min": None, "q1": None, "median": None, "q3": None, "max": None, "mean": None, "sd": None}
    mean = math.fsum(xs) / n
    sd = math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {
        "n": n,
        "min": _r(xs[0], nd),
        "q1": _r(quantile(xs, 0.25), nd),
        "median": _r(quantile(xs, 0.5), nd),
        "q3": _r(quantile(xs, 0.75), nd),
        "max": _r(xs[-1], nd),
        "mean": _r(mean, nd),
        "sd": _r(sd, nd),
    }


def histogram(values: Iterable[Optional[float]], edges: Sequence[float]) -> List[Dict[str, Any]]:
    """Bins [low, high); the last one is open. Values below the first edge → first bin."""
    counts = [0] * len(edges)
    for v in values:
        if v is None:
            continue
        idx = 0
        for i, e in enumerate(edges):
            if v >= e:
                idx = i
        counts[idx] += 1
    out = []
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else None
        out.append({"lo": lo, "hi": hi, "count": counts[i]})
    return out


def vaf_histogram(values: Iterable[Optional[float]]) -> List[Dict[str, Any]]:
    """20 bins of 0.05; VAF = 1.0 is counted in the last bin [0.95, 1.0]."""
    counts = [0] * 20
    for v in values:
        if v is None:
            continue
        counts[min(int(v * 20 + 1e-9), 19)] += 1
    return [{"lo": VAF_EDGES[i], "hi": VAF_EDGES[i + 1], "count": counts[i]} for i in range(20)]


def _sorted_counter(c: Counter) -> Dict[str, int]:
    return {k: c[k] for k in sorted(c, key=lambda k: (-c[k], str(k)))}


def _ratio(a: int, b: int) -> Optional[float]:
    return _r(a / b, 3) if b else None


def _is_transition(ref: str, alt: str) -> bool:
    return (ref, alt) in _TRANSITIONS


def _gq(v: Variant) -> Optional[float]:
    raw = v.format_data.get("GQ")
    try:
        return None if raw in (None, "", ".") else float(raw)
    except (TypeError, ValueError):
        return None


def _filters(v: Variant) -> List[str]:
    f = v.filter_status or "."
    if f in ("PASS", "."):
        return ["PASS"]
    return sorted(x for x in f.split(";") if x)


def _check(name: str, label: str, value: Any, expected: str, ok: Optional[bool], explanation: str) -> Dict[str, Any]:
    status = STATUS_NA if ok is None else (STATUS_OK if ok else STATUS_WARN)
    return {"id": name, "label": label, "value": value, "expected": expected, "status": status, "explanation": explanation}


# --- Shared metrics ---------------------------------------------------------------
class _Accumulator:
    """Streaming counters (usable on a whole VCF without keeping it in memory)."""

    def __init__(self) -> None:
        self.records = 0
        self.called = 0
        self.types: Counter = Counter()
        self.zygosity: Counter = Counter()
        self.filters: Counter = Counter()
        self.chromosomes: Counter = Counter()
        self.ts = 0
        self.tv = 0
        self.pass_count = 0
        self.multiallelic = 0

    def add(self, v: Variant) -> None:
        self.records += 1
        if not v.is_called:
            return
        self.called += 1
        vt = v.variant_type
        self.types[vt] += 1
        self.zygosity[v.zygosity or "unknown"] += 1
        self.chromosomes[v.chromosome] += 1
        fl = _filters(v)
        for f in fl:
            self.filters[f] += 1
        if fl == ["PASS"]:
            self.pass_count += 1
            if vt == "SNV":
                if _is_transition(v.ref, v.alt):
                    self.ts += 1
                else:
                    self.tv += 1
        if v.alt_index > 1:
            self.multiallelic += 1

    def to_dict(self) -> Dict[str, Any]:
        het = self.zygosity.get("heterozygous", 0)
        hom = self.zygosity.get("homozygous", 0)
        return {
            "records_read": self.records,
            "alleles_called": self.called,
            "pass": self.pass_count,
            "pass_rate": _ratio(self.pass_count, self.called),
            "by_type": _sorted_counter(self.types),
            "by_zygosity": _sorted_counter(self.zygosity),
            "by_filter": _sorted_counter(self.filters),
            "by_chromosome": {k: self.chromosomes[k] for k in sorted(self.chromosomes, key=chrom_sort_key)},
            "transitions_pass_snv": self.ts,
            "transversions_pass_snv": self.tv,
            "ti_tv": _ratio(self.ts, self.tv),
            "het_hom_ratio": _ratio(het, hom),
            "multiallelic_split_alleles": self.multiallelic,
        }


def summarize_file(variants: Iterable[Variant]) -> Dict[str, Any]:
    """Summary of the whole VCF (streamed)."""
    acc = _Accumulator()
    for v in variants:
        acc.add(v)
    return acc.to_dict()


# --- Panel statistics ------------------------------------------------------------
def _variant_row(f: Finding) -> Dict[str, Any]:
    v, c = f.variant, f.clinvar
    indel_len = abs(len(v.alt) - len(v.ref)) if v.variant_type in ("Insertion", "Deletion") else None
    return {
        "gene": f.gene.symbol,
        "chromosome": v.chromosome,
        "position": v.position,
        "ref": v.ref,
        "alt": v.alt,
        "variant_type": v.variant_type,
        "indel_length": indel_len,
        "genotype": v.genotype,
        "zygosity": v.zygosity,
        "quality": _r(v.quality, 2),
        "dp": v.depth,
        "gq": _gq(v),
        "vaf": _r(v.vaf, 4),
        "filter": v.filter_status,
        "qc_status": f.qc.status,
        "qc_flags": list(f.qc.flags),
        "category": f.category,
        "clinvar_significance": c.significance.value if c else None,
        "review_stars": c.stars if c else None,
        "variation_id": c.variation_id if c else None,
        "rsid": c.rsid if c else None,
        "hgvs": c.hgvs if c else None,
        "conditions": c.conditions if c else None,
    }


def _per_gene(genes: Sequence[str], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    table = []
    for g in genes:
        rs = [r for r in rows if r["gene"] == g]
        cats = Counter(r["category"] for r in rs)
        table.append({
            "gene": g,
            "variants": len(rs),
            "pass": sum(1 for r in rs if r["filter"] in ("PASS", ".")),
            "qc_pass": sum(1 for r in rs if r["qc_status"] == "PASS"),
            "snv": sum(1 for r in rs if r["variant_type"] == "SNV"),
            "indel": sum(1 for r in rs if r["variant_type"] in ("Insertion", "Deletion", "Complex")),
            "categories": {k: cats.get(k, 0) for k in CATEGORIES},
            "median_dp": describe(r["dp"] for r in rs)["median"],
        })
    return table


def quality_checks(
    panel_acc: Dict[str, Any],
    het_vaf: Dict[str, Any],
    dp: Dict[str, Any],
    min_depth: int,
    n_snv_pass: int,
    alignment_qc: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    titv = panel_acc.get("ti_tv")
    enough = n_snv_pass >= MIN_SNV_FOR_RATIOS
    checks.append(_check(
        "ti_tv", "Transition/transversion ratio (PASS SNVs)", titv,
        f"{TITV_RANGE[0]}–{TITV_RANGE[1]} (≥ {MIN_SNV_FOR_RATIOS} SNVs)",
        (TITV_RANGE[0] <= titv <= TITV_RANGE[1]) if (enough and titv is not None) else None,
        "A low Ti/Tv indicates false positives (random errors ≈ 0.5); ≈ 2.0 genome-wide, ≈ 3.0 coding exome.",
    ))
    hh = panel_acc.get("het_hom_ratio")
    checks.append(_check(
        "het_hom", "Heterozygous/homozygous ratio", hh,
        f"{HET_HOM_RANGE[0]}–{HET_HOM_RANGE[1]} (≥ {MIN_SNV_FOR_RATIOS} variants)",
        (HET_HOM_RANGE[0] <= hh <= HET_HOM_RANGE[1]) if (panel_acc.get("alleles_called", 0) >= MIN_SNV_FOR_RATIOS and hh is not None) else None,
        "Too high: possible contamination; too low: consanguinity or loss of heterozygosity.",
    ))
    med = het_vaf.get("median")
    checks.append(_check(
        "het_vaf_median", "Median heterozygous VAF", med,
        f"{HET_VAF_MEDIAN_RANGE[0]}–{HET_VAF_MEDIAN_RANGE[1]} (≥ {MIN_HET_FOR_VAF} heterozygotes)",
        (HET_VAF_MEDIAN_RANGE[0] <= med <= HET_VAF_MEDIAN_RANGE[1]) if (het_vaf.get("n", 0) >= MIN_HET_FOR_VAF and med is not None) else None,
        "A germline heterozygote is expected near 0.5; a shift suggests contamination or capture bias.",
    ))
    pr = panel_acc.get("pass_rate")
    checks.append(_check(
        "pass_rate", "Share of PASS variants (GATK filters)", pr, f"≥ {MIN_PASS_RATE}",
        (pr >= MIN_PASS_RATE) if pr is not None else None,
        "Many filtered variants: degraded sequencing or alignment quality.",
    ))
    mdp = dp.get("median")
    checks.append(_check(
        "median_dp", "Median depth at variants", mdp, f"≥ {min_depth} (clinical threshold)",
        (mdp >= min_depth) if mdp is not None else None,
        "Insufficient depth: reduced sensitivity, less reliable genotypes.",
    ))
    if alignment_qc:
        mapped = alignment_qc.get("mapped_rate")
        checks.append(_check(
            "mapped_rate", "Reads mapped to hg38", mapped, "≥ 0.95",
            (mapped >= 0.95) if mapped is not None else None,
            "A low rate suggests contamination, a wrong reference or untrimmed adapters.",
        ))
        dup = alignment_qc.get("duplication_rate")
        checks.append(_check(
            "duplication_rate", "Duplicate rate (MarkDuplicates)", dup, "≤ 0.30",
            (dup <= 0.30) if dup is not None else None,
            "High rate: low library complexity, overestimated useful depth.",
        ))
        cov = alignment_qc.get("clinvar_sites_coverage") or {}
        frac = cov.get("fraction_covered")
        checks.append(_check(
            "pathogenic_sites_coverage",
            f"Panel ClinVar P/LP sites covered ≥ {cov.get('min_depth', min_depth)}x",
            frac, f"≥ {MIN_SITE_COVERAGE_FRACTION}",
            (frac >= MIN_SITE_COVERAGE_FRACTION) if frac is not None else None,
            "A poorly covered known pathogenic site cannot be excluded: no variant there does not mean no mutation.",
        ))
    return checks


def panel_statistics(
    analysis: PanelAnalysis,
    file_summary: Optional[Dict[str, Any]] = None,
    alignment_qc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    findings = analysis.all_findings
    rows = [_variant_row(f) for f in findings]

    acc = _Accumulator()
    for f in findings:
        acc.add(f.variant)
    panel_acc = acc.to_dict()

    pass_rows = [r for r in rows if r["filter"] in ("PASS", ".")]
    het_vaf = describe(r["vaf"] for r in rows if r["zygosity"] == "heterozygous")
    dp = describe(r["dp"] for r in rows)
    n_snv_pass = panel_acc["transitions_pass_snv"] + panel_acc["transversions_pass_snv"]
    categories = Counter(r["category"] for r in rows)
    flags = Counter(flag for r in rows for flag in r["qc_flags"])
    annotated = sum(1 for r in rows if r["clinvar_significance"] is not None)

    return {
        "version": STATISTICS_VERSION,
        "file": file_summary,
        "panel": {
            **panel_acc,
            "variants_in_panel": len(rows),
            "clinvar_annotated": annotated,
            "clinvar_annotated_rate": _ratio(annotated, len(rows)),
            "clinical_qc_pass": sum(1 for r in rows if r["qc_status"] == "PASS"),
            "clinical_qc_pass_rate": _ratio(sum(1 for r in rows if r["qc_status"] == "PASS"), len(rows)),
            "by_category": {k: categories.get(k, 0) for k in CATEGORIES},
            "qc_flags": _sorted_counter(flags),
        },
        "distributions": {
            "quality": {"summary": describe(r["quality"] for r in rows), "histogram": histogram((r["quality"] for r in rows), QUAL_EDGES)},
            "depth": {"summary": dp, "histogram": histogram((r["dp"] for r in rows), DP_EDGES)},
            "genotype_quality": {"summary": describe(r["gq"] for r in rows), "histogram": histogram((r["gq"] for r in rows), GQ_EDGES)},
            "vaf_heterozygous": {"summary": het_vaf, "histogram": vaf_histogram(r["vaf"] for r in rows if r["zygosity"] == "heterozygous")},
            "vaf_all": {"summary": describe(r["vaf"] for r in rows), "histogram": vaf_histogram(r["vaf"] for r in rows)},
            "indel_length": {
                "summary": describe(r["indel_length"] for r in rows),
                "histogram": histogram((r["indel_length"] for r in rows), INDEL_LEN_EDGES),
            },
            "pass_only_quality": describe(r["quality"] for r in pass_rows),
        },
        "per_gene": _per_gene(analysis.panel_genes, rows),
        "quality_checks": quality_checks(panel_acc, het_vaf, dp, analysis.thresholds.min_depth, n_snv_pass, alignment_qc),
        "alignment": alignment_qc,
        "variants": rows,
    }
