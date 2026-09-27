"""Interpretation of the VCF statistics by BioGPT: facts, reference text, verification, final answer.

Workflow (deterministic parts only; the model itself lives in src.llm.stats_model):

1. `extract_facts`   vcf_statistics.json + risk decision → compact, rounded facts
                     (values + status OK / LOW / HIGH / NA against the expected ranges of
                     src.genomics.statistics). These facts are the model input.
2. `render_prompt`   facts → linear text prompt (the same format at training and inference).
3. `reference_sentences`  deterministic expert interpreter: one sentence per topic, with
                     paraphrase variants. Variant 0 is the fallback text of the final answer;
                     random variants are the training targets of the fine-tuned model.
4. `verify_interpretation`  every generated sentence is checked against the facts: each number
                     must equal the fact of the metric it refers to (or a documented threshold),
                     each status word (within / below / above / could not be assessed) must match
                     the computed status, each risk level must equal the rule-based level, each
                     gene must hold the stated role (confirmed / to confirm / not excluded).
                     Sentences with no verifiable claim, unassignable numbers, negations or
                     clinical advice (therapy, prognosis, screening…) are rejected.
5. `assemble_final`  final answer = for each required topic, the first verified model sentence,
                     otherwise the reference sentence. The final text is re-verified: it is
                     correct by construction.

The model never decides anything: the facts and the risk level come from deterministic code.
Same facts + same model output → same verdict and same final text.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.genomics.statistics import (
    HET_HOM_RANGE,
    HET_VAF_MEDIAN_RANGE,
    MIN_HET_FOR_VAF,
    MIN_PASS_RATE,
    MIN_SITE_COVERAGE_FRACTION,
    MIN_SNV_FOR_RATIOS,
    TITV_RANGE,
)

FACTS_VERSION = "germlineiq-stats-facts-v1"
REFERENCE_VERSION = "germlineiq-stats-reference-v2"  # v2: ranges written "a to b" (BioGPT drops "–")
VERIFIER_VERSION = "germlineiq-stats-verifier-v1"

MIN_MAPPED_RATE = 0.95
MAX_DUPLICATION_RATE = 0.30
MIN_GENE_SITE_COVERAGE = 0.95
DEFAULT_MIN_DEPTH = 15

OK, LOW, HIGH, NA = "OK", "LOW", "HIGH", "NA"
RISK_LEVELS = ("HIGH", "MODERATE", "LOW", "INDETERMINATE")


# --- 1. Facts --------------------------------------------------------------------
def _round(x: Optional[float], nd: int) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def _range_status(value: Optional[float], lo: Optional[float], hi: Optional[float], assessable: bool = True) -> str:
    if value is None or not assessable:
        return NA
    if lo is not None and value < lo:
        return LOW
    if hi is not None and value > hi:
        return HIGH
    return OK


def compute_statuses(f: Dict[str, Any]) -> Dict[str, str]:
    """Status of every metric, with the thresholds of src.genomics.statistics.quality_checks."""
    min_dp = f.get("min_depth") or DEFAULT_MIN_DEPTH
    return {
        "pass_rate": _range_status(f.get("pass_rate"), MIN_PASS_RATE, None),
        "ti_tv": _range_status(f.get("ti_tv"), *TITV_RANGE, assessable=(f.get("snv_pass") or 0) >= MIN_SNV_FOR_RATIOS),
        "het_hom": _range_status(f.get("het_hom"), *HET_HOM_RANGE, assessable=(f.get("variants") or 0) >= MIN_SNV_FOR_RATIOS),
        "het_vaf": _range_status(f.get("het_vaf_median"), *HET_VAF_MEDIAN_RANGE, assessable=(f.get("n_het") or 0) >= MIN_HET_FOR_VAF),
        "median_depth": _range_status(f.get("median_depth"), min_dp, None),
        "mapped_rate": _range_status(f.get("mapped_rate"), MIN_MAPPED_RATE, None),
        "duplication_rate": _range_status(f.get("duplication_rate"), None, MAX_DUPLICATION_RATE),
        "site_coverage": _range_status(f.get("site_coverage"), MIN_SITE_COVERAGE_FRACTION, None),
    }


def finalize_facts(f: Dict[str, Any]) -> Dict[str, Any]:
    """Rounds values, sorts gene lists and computes the statuses (single normalisation path)."""
    out = dict(f)
    out["status"] = compute_statuses(out)  # on the raw values, like quality_checks
    for k, nd in (("pass_rate", 4), ("mapped_rate", 4), ("duplication_rate", 4), ("site_coverage", 4),
                  ("ti_tv", 3), ("het_hom", 3), ("het_vaf_median", 3), ("median_depth", 1)):
        out[k] = _round(out.get(k), nd)
    for k in ("confirmed", "to_confirm", "genes_not_excluded"):
        out[k] = sorted(set(out.get(k) or []))
    out["version"] = FACTS_VERSION
    return out


def extract_facts(
    stats: Dict[str, Any],
    risk_level: str,
    confirmed_genes: Iterable[str] = (),
    to_confirm_genes: Iterable[str] = (),
) -> Dict[str, Any]:
    """vcf_statistics.json (src.genomics.statistics.panel_statistics) + risk decision → facts."""
    panel = stats.get("panel") or {}
    by_type = panel.get("by_type") or {}
    dist = stats.get("distributions") or {}
    alignment = stats.get("alignment") or None
    coverage = (alignment or {}).get("clinvar_sites_coverage") or {}
    het = (dist.get("vaf_heterozygous") or {}).get("summary") or {}
    depth = (dist.get("depth") or {}).get("summary") or {}
    weak = [
        g["gene"] for g in coverage.get("per_gene", [])
        if g.get("fraction_covered") is not None and g["fraction_covered"] < MIN_GENE_SITE_COVERAGE
    ]
    facts = {
        "mode": "FASTQ" if alignment else "VCF",
        "variants": int(panel.get("variants_in_panel") or 0),
        "snv": int(by_type.get("SNV", 0)),
        "indel": int(by_type.get("Insertion", 0)) + int(by_type.get("Deletion", 0)),
        "snv_pass": int(panel.get("transitions_pass_snv", 0)) + int(panel.get("transversions_pass_snv", 0)),
        "n_het": int(het.get("n") or 0),
        "pass_rate": panel.get("pass_rate"),
        "ti_tv": panel.get("ti_tv"),
        "het_hom": panel.get("het_hom_ratio"),
        "het_vaf_median": het.get("median"),
        "median_depth": depth.get("median"),
        "min_depth": coverage.get("min_depth") or DEFAULT_MIN_DEPTH,
        "mapped_rate": (alignment or {}).get("mapped_rate"),
        "duplication_rate": (alignment or {}).get("duplication_rate"),
        "site_coverage": coverage.get("fraction_covered"),
        "genes_not_excluded": weak,
        "confirmed": list(confirmed_genes),
        "to_confirm": list(to_confirm_genes),
        "low_vaf_calls": int((panel.get("qc_flags") or {}).get("LOW_VAF_MOSAIC_OR_CHIP", 0)),
        "risk_level": risk_level,
    }
    return finalize_facts(facts)


def facts_sha256(facts: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


# --- Number formatting (shared by the prompt and the reference text) ---------------
def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def ratio(x: float) -> str:
    return f"{x:.2f}"


def depth_x(x: float) -> str:
    return f"{x:.0f}x" if float(x).is_integer() else f"{x:.1f}x"


def join_genes(genes: Sequence[str]) -> str:
    genes = list(genes)
    return genes[0] if len(genes) == 1 else ", ".join(genes[:-1]) + " and " + genes[-1]


# --- 2. Prompt ---------------------------------------------------------------------
def render_prompt(f: Dict[str, Any]) -> str:
    s = f["status"]

    def val(fmt, key, status_key):
        return f"{fmt(f[key])} ({s[status_key]})" if f.get(key) is not None else "NA (NA)"

    parts = [
        f"VCF statistics. mode: {f['mode']}.",
        f"panel variants: {f['variants']} (SNV {f['snv']}, indel {f['indel']}).",
        f"PASS rate: {val(pct, 'pass_rate', 'pass_rate')}.",
        f"Ti/Tv: {val(ratio, 'ti_tv', 'ti_tv')}.",
        f"het/hom: {val(ratio, 'het_hom', 'het_hom')}.",
        f"median heterozygous VAF: {val(ratio, 'het_vaf_median', 'het_vaf')}.",
        f"median depth: {val(depth_x, 'median_depth', 'median_depth')}.",
    ]
    if f["mode"] == "FASTQ":
        parts += [
            f"mapped reads: {val(pct, 'mapped_rate', 'mapped_rate')}.",
            f"duplicates: {val(pct, 'duplication_rate', 'duplication_rate')}.",
            f"known pathogenic sites covered: {val(pct, 'site_coverage', 'site_coverage')}.",
        ]
    parts += [
        f"not excluded: {', '.join(f['genes_not_excluded']) or 'none'}.",
        f"confirmed P/LP: {', '.join(f['confirmed']) or 'none'}.",
        f"to confirm: {', '.join(f['to_confirm']) or 'none'}.",
        f"low-VAF calls: {f['low_vaf_calls']}.",
        f"risk: {f['risk_level']}.",
        "Interpretation:",
    ]
    return " ".join(parts)


# --- 3. Reference interpreter --------------------------------------------------------
# Topic order of the final answer
TOPICS = (
    "overview", "pass_rate", "ti_tv", "het_hom", "het_vaf", "median_depth", "mapped_rate",
    "duplication_rate", "site_coverage", "not_excluded", "confirmed", "to_confirm", "low_vaf", "risk",
)


def required_topics(f: Dict[str, Any]) -> List[str]:
    topics = ["overview", "pass_rate", "ti_tv", "het_hom", "het_vaf"]
    if f.get("median_depth") is not None:
        topics.append("median_depth")
    if f["mode"] == "FASTQ":
        topics += [t for t in ("mapped_rate", "duplication_rate") if f.get(t) is not None]
    topics.append("site_coverage")
    if f["genes_not_excluded"]:
        topics.append("not_excluded")
    topics.append("confirmed")
    if f["to_confirm"]:
        topics.append("to_confirm")
    if f["low_vaf_calls"]:
        topics.append("low_vaf")
    topics.append("risk")
    return topics


def _variants(f: Dict[str, Any], topic: str) -> List[str]:
    """Paraphrase variants of the reference sentence of a topic (variant 0 = fallback text)."""
    s = f["status"]
    if topic == "overview":
        n, snv, indel = f["variants"], f["snv"], f["indel"]
        return [
            f"{n} variants were called in the panel genes: {snv} SNVs and {indel} indels.",
            f"The panel contains {n} variant calls ({snv} SNVs, {indel} indels).",
            f"In total, {n} variants were identified in the panel ({snv} SNVs and {indel} indels).",
        ]
    if topic == "pass_rate":
        pr = pct(f["pass_rate"])
        if s["pass_rate"] == OK:
            return [
                f"{pr} of the calls passed the GATK filters, which is within the expected range (≥ 80%).",
                f"The PASS rate is {pr}, within the expected range (≥ 80%).",
                f"{pr} of the calls passed the GATK filters, as expected for good-quality data.",
            ]
        return [
            f"Only {pr} of the calls passed the GATK filters, below the expected minimum of 80%; sequencing or alignment quality should be reviewed.",
            f"The PASS rate is {pr}, below the expected minimum of 80%, which suggests degraded sequencing or alignment quality.",
        ]
    if topic == "ti_tv":
        st = s["ti_tv"]
        if st == NA:
            return ["The Ti/Tv ratio could not be assessed (too few SNVs)."]
        t = ratio(f["ti_tv"])
        if st == OK:
            return [
                f"The Ti/Tv ratio of {t} is within the expected range (1.8 to 3.3).",
                f"Ti/Tv is {t}, within the expected range for human germline calls (1.8 to 3.3).",
                f"The transition/transversion ratio ({t}) is within the expected range.",
            ]
        if st == LOW:
            return [
                f"The Ti/Tv ratio of {t} is below the expected range (1.8 to 3.3), which may indicate false-positive calls.",
                f"Ti/Tv is {t}, below the expected range (1.8 to 3.3); false-positive calls should be reviewed.",
            ]
        return [
            f"The Ti/Tv ratio of {t} is above the expected range (1.8 to 3.3) and should be reviewed.",
            f"Ti/Tv is {t}, above the expected range (1.8 to 3.3), which should be reviewed.",
        ]
    if topic == "het_hom":
        st = s["het_hom"]
        if st == NA:
            return ["The het/hom ratio could not be assessed (too few variants)."]
        h = ratio(f["het_hom"])
        if st == OK:
            return [
                f"The heterozygous/homozygous ratio of {h} is within the expected range (1.0 to 3.0).",
                f"The het/hom ratio ({h}) is within the expected range.",
                f"With a het/hom ratio of {h}, zygosity is consistent with a germline sample.",
            ]
        if st == HIGH:
            return [
                f"The het/hom ratio of {h} is above the expected range (1.0 to 3.0), which may indicate sample contamination.",
                f"The heterozygous/homozygous ratio ({h}) is above the expected range, which may indicate contamination.",
            ]
        return [
            f"The het/hom ratio of {h} is below the expected range (1.0 to 3.0), which may indicate consanguinity or loss of heterozygosity.",
            f"The heterozygous/homozygous ratio ({h}) is below the expected range, which may indicate loss of heterozygosity.",
        ]
    if topic == "het_vaf":
        st = s["het_vaf"]
        if st == NA:
            return ["The median heterozygous VAF could not be assessed (too few heterozygous calls)."]
        v = ratio(f["het_vaf_median"])
        if st == OK:
            return [
                f"The median heterozygous VAF of {v} is within the expected range (0.40 to 0.60).",
                f"Heterozygous calls have a median allele fraction of {v}, consistent with germline heterozygosity.",
                f"The median heterozygous VAF ({v}) is within the expected range.",
            ]
        side = "below" if st == LOW else "above"
        return [
            f"The median heterozygous VAF of {v} is {side} the expected range (0.40 to 0.60), which may indicate contamination or capture bias.",
            f"The median heterozygous VAF ({v}) is outside the expected range (0.40 to 0.60), which may indicate contamination or capture bias.",
        ]
    md = int(f.get("min_depth") or DEFAULT_MIN_DEPTH)
    if topic == "median_depth":
        d = depth_x(f["median_depth"])
        if s["median_depth"] == OK:
            return [
                f"The median depth at variant sites is {d}, above the clinical threshold of {md}x.",
                f"Median sequencing depth is {d}, which meets the clinical threshold ({md}x).",
                f"Variant sites have a median depth of {d}, adequate for clinical calling.",
            ]
        return [
            f"The median depth at variant sites is {d}, below the clinical threshold of {md}x; genotypes are less reliable.",
            f"Median sequencing depth is only {d}, below the clinical threshold ({md}x).",
        ]
    if topic == "mapped_rate":
        m = pct(f["mapped_rate"])
        if s["mapped_rate"] == OK:
            return [
                f"{m} of reads mapped to hg38, within the expected range (≥ 95%).",
                f"The mapping rate is {m}, as expected.",
                f"{m} of reads were mapped to the reference genome, within the expected range.",
            ]
        return [
            f"Only {m} of reads mapped to hg38, below the expected minimum of 95%, which may indicate contamination or a wrong reference.",
            f"The mapping rate is {m}, below the expected minimum of 95%.",
        ]
    if topic == "duplication_rate":
        d = pct(f["duplication_rate"])
        if s["duplication_rate"] == OK:
            return [
                f"The duplicate rate is {d}, within the expected range (≤ 30%).",
                f"Duplicates account for {d} of read pairs, which is acceptable.",
                f"Library complexity is adequate, with a duplicate rate of {d}.",
            ]
        return [
            f"The duplicate rate of {d} is above the recommended maximum of 30%, indicating low library complexity.",
            f"Duplicates account for {d} of read pairs, above the recommended maximum of 30%.",
        ]
    if topic == "site_coverage":
        st = s["site_coverage"]
        if f["mode"] == "VCF" or st == NA:
            return ["Coverage of known pathogenic sites cannot be measured in VCF mode."]
        c = pct(f["site_coverage"])
        if st == OK:
            return [
                f"{c} of known pathogenic sites in the panel are covered at {md}x or more, within the expected range (≥ 95%).",
                f"Known pathogenic sites are well covered: {c} reach {md}x, within the expected range.",
                f"{c} of known pathogenic sites are covered at {md}x or more, as expected.",
            ]
        return [
            f"Only {c} of known pathogenic sites in the panel are covered at {md}x or more, below the expected minimum of 95%.",
            f"Coverage of known pathogenic sites is insufficient: {c} reach {md}x, below the expected minimum of 95%.",
        ]
    if topic == "not_excluded":
        g = join_genes(f["genes_not_excluded"])
        return [
            f"Coverage is incomplete for {g}: an uncovered known pathogenic variant cannot be excluded in this region.",
            f"A known pathogenic variant cannot be excluded in {g} because of incomplete coverage.",
        ]
    if topic == "confirmed":
        if not f["confirmed"]:
            return [
                "No pathogenic or likely pathogenic variant was confirmed in the germline panel genes.",
                "No confirmed pathogenic or likely pathogenic variant was found in the panel.",
            ]
        g = join_genes(f["confirmed"])
        return [
            f"A pathogenic or likely pathogenic variant was confirmed in {g}.",
            f"Confirmed pathogenic or likely pathogenic variants were found in {g}.",
            f"The analysis confirmed a pathogenic or likely pathogenic variant in {g}.",
        ]
    if topic == "to_confirm":
        g = join_genes(f["to_confirm"])
        return [
            f"A variant in {g} requires orthogonal confirmation before clinical use.",
            f"Variants in {g} require orthogonal confirmation.",
        ]
    if topic == "low_vaf":
        k = f["low_vaf_calls"]
        return [
            f"{k} calls have a low allele fraction (possible mosaicism or clonal haematopoiesis) and are flagged for review.",
            f"{k} low-VAF calls are flagged for review (possible mosaicism or clonal haematopoiesis).",
        ]
    if topic == "risk":
        r = f["risk_level"]
        return [
            f"The deterministic rules classify the hereditary breast cancer risk as {r}.",
            f"Under the deterministic rules, the risk level is {r}.",
            f"Hereditary breast cancer risk: {r} (deterministic rules).",
        ]
    raise ValueError(f"unknown topic: {topic}")


def reference_sentences(f: Dict[str, Any], rng: Optional[random.Random] = None) -> List[Tuple[str, str]]:
    """[(topic, sentence)] in topic order. rng=None → variant 0 of every topic (fallback text)."""
    out = []
    for topic in required_topics(f):
        options = _variants(f, topic)
        out.append((topic, options[rng.randrange(len(options))] if rng else options[0]))
    return out


def reference_text(f: Dict[str, Any], rng: Optional[random.Random] = None) -> str:
    return " ".join(s for _, s in reference_sentences(f, rng))


# --- 4. Verification -----------------------------------------------------------------
# Metric mentions. Longest match wins on overlaps (e.g. "low allele fraction" before "allele fraction").
_METRIC_PATTERNS: Dict[str, str] = {
    "low_vaf": r"low[- ]VAF|low allele fraction",
    "variants": r"variant calls|variants? (?:were |was )?(?:called|identified|detected) in the panel|variants were called|panel contains",
    "snv": r"\bSNVs?\b",
    "indel": r"\bindels?\b",
    "pass_rate": r"\bPASS rate\b|passed (?:the )?(?:GATK )?filters",
    "ti_tv": r"Ti/Tv|transition/transversion",
    "het_hom": r"het(?:erozygous)?/hom(?:ozygous)?",
    "het_vaf": r"heterozygous VAF|median allele fraction",
    "median_depth": r"median (?:sequencing )?depth|median depth",
    "mapped_rate": r"\bmapped\b|mapping rate",
    "duplication_rate": r"duplicate rate|duplicates account|\bduplicates?\b",
    "site_coverage": r"known pathogenic sites",
}
_METRIC_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _METRIC_PATTERNS.items()}
STATUS_METRICS = ("pass_rate", "ti_tv", "het_hom", "het_vaf", "median_depth", "mapped_rate", "duplication_rate", "site_coverage")
COUNT_METRICS = {"variants": "variants", "snv": "snv", "indel": "indel", "low_vaf": "low_vaf_calls"}
PERCENT_METRICS = {"pass_rate", "mapped_rate", "duplication_rate", "site_coverage"}

# Thresholds a sentence may quote for each metric (in the metric's own unit)
_THRESHOLDS: Dict[str, Tuple[float, ...]] = {
    "pass_rate": (MIN_PASS_RATE,),
    "ti_tv": TITV_RANGE,
    "het_hom": HET_HOM_RANGE,
    "het_vaf": HET_VAF_MEDIAN_RANGE + (0.5,),
    "median_depth": (DEFAULT_MIN_DEPTH,),
    "mapped_rate": (MIN_MAPPED_RATE,),
    "duplication_rate": (MAX_DUPLICATION_RATE,),
    "site_coverage": (MIN_SITE_COVERAGE_FRACTION, DEFAULT_MIN_DEPTH),
}

_OK_RE = re.compile(
    r"within the expected range|as expected|meets the clinical threshold|above the clinical threshold|"
    r"\bacceptable\b|\badequate\b|consistent with (?:a )?germline|well covered", re.IGNORECASE)
_LOW_RE = re.compile(r"\bbelow the (?:expected|clinical|recommended)\b|\binsufficient\b", re.IGNORECASE)
_HIGH_RE = re.compile(r"above the (?:expected range|recommended maximum)|\bexceeds\b", re.IGNORECASE)
_OUT_RE = re.compile(r"outside the expected range", re.IGNORECASE)
_NA_RE = re.compile(r"could not be assessed|cannot be measured", re.IGNORECASE)

_RISK_RE = re.compile(
    r"\b(high|moderate|low|indeterminate)\b(?=[- ]risk)|"
    r"\brisk(?: level)?(?: is| was| as| of|:)? (high|moderate|low|indeterminate)\b|"
    r"\b(HIGH|MODERATE|LOW|INDETERMINATE)\b",
)
_CONFIRMED_RE = re.compile(
    r"\bconfirmed in\b|\bconfirmed a pathogenic\b|pathogenic or likely pathogenic variants? (?:was|were) (?:confirmed|found) in\b",
    re.IGNORECASE)
_NONE_CONFIRMED_RE = re.compile(r"\bno (?:confirmed )?pathogenic or likely pathogenic variants?\b", re.IGNORECASE)
_TO_CONFIRM_RE = re.compile(r"\brequires? (?:orthogonal )?confirmation\b|\bneeds? (?:orthogonal )?confirmation\b", re.IGNORECASE)
_NOT_EXCLUDED_RE = re.compile(r"\bcannot be excluded\b|coverage is incomplete|incomplete coverage", re.IGNORECASE)
_FORBIDDEN_RE = re.compile(
    r"\b(?:treat\w*|therap\w*|PARP|chemotherap\w*|surgery|surgical|mastectomy|oophorectomy|salpingo\w*|"
    r"prognos\w*|survival|lifetime|will develop|diagnos(?:ed|is)|screening|MRI|mammogra\w*|cure\w*|"
    r"recommend(?:ed|s)? (?:that|to)|should undergo|family members?)\b",
    re.IGNORECASE)
# Negations are rejected (unverifiable) except in these fixed phrases
_ALLOWED_NEGATIONS = re.compile(
    r"cannot be excluded|could not be assessed|cannot be measured|\bno (?:confirmed )?pathogenic or likely pathogenic variants?\b",
    re.IGNORECASE)
_NEGATION_RE = re.compile(r"\bnot\b|\bno\b|n't\b|\bnever\b|\bwithout\b|\bnone\b|\bcannot\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w.,/-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(\s?%|x\b)?")


def normalize_generated_text(text: str) -> str:
    """Undo the spacing of the BioGPT detokenizer ("Ti / Tv" → "Ti/Tv", "( 2.04 )" → "(2.04)").

    Whitespace and punctuation spacing only: no word, number or symbol is added or removed.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"(?<=\w) / (?=\w)", "/", text)
    text = re.sub(r"\( ", "(", text)
    text = re.sub(r" \)", ")", text)
    text = re.sub(r"(?<=\d) %", "%", text)
    text = re.sub(r" ([,;:.])(?=\s|$)", r"\1", text)
    return text


def split_sentences(text: str) -> List[str]:
    """Split on '.', ';', '!' and '?' (not on decimal points such as '2.04')."""
    text = re.sub(r"\s+", " ", text or "").strip()
    out, buf = [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        buf += ch
        is_decimal = ch == "." and i > 0 and text[i - 1].isdigit() and i + 1 < len(text) and text[i + 1].isdigit()
        if ch in ".!?" and not is_decimal:
            out.append(buf.strip())
            buf = ""
        i += 1
    if buf.strip():
        out.append(buf.strip())  # unfinished trailing fragment: verified (and rejected) like the rest
    return [s for s in out if s]


@dataclass
class SentenceVerdict:
    sentence: str
    verified: bool
    reasons: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    claims: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"sentence": self.sentence, "verified": self.verified, "reasons": self.reasons,
                "topics": self.topics, "claims": self.claims}


def _mentions(sentence: str) -> List[Tuple[str, int, int]]:
    found = []
    for metric, rx in _METRIC_RE.items():
        for m in rx.finditer(sentence):
            found.append((metric, m.start(), m.end()))
    found.sort(key=lambda t: (t[1], -(t[2] - t[1])))
    kept: List[Tuple[str, int, int]] = []
    for m in found:
        if kept and m[1] < kept[-1][2]:  # overlap: keep the longest
            if (m[2] - m[1]) > (kept[-1][2] - kept[-1][1]):
                kept[-1] = m
            continue
        kept.append(m)
    return kept


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _number_ok(metric: str, token: str, unit: str, facts: Dict[str, Any]) -> Tuple[bool, str]:
    value = float(token.replace(",", ""))
    nd = _decimals(token)
    if metric in COUNT_METRICS:
        expected = facts[COUNT_METRICS[metric]]
        return (unit == "" and "." not in token and int(value) == expected), f"{token} vs {expected}"
    key = {"het_vaf": "het_vaf_median"}.get(metric, metric)
    fact = facts.get(key)
    candidates: List[float] = list(_THRESHOLDS.get(metric, ()))
    if metric in ("median_depth", "site_coverage"):
        candidates.append(float(facts.get("min_depth") or DEFAULT_MIN_DEPTH))
    if fact is not None:
        candidates.append(fact)
    for c in candidates:
        if metric in PERCENT_METRICS and unit.strip() == "%":
            ref = c * 100 if c <= 1 else None  # the depth threshold (15) is never a percentage
            if ref is not None and round(ref, nd) == value:
                return True, f"{token}% ≈ {c}"
        elif metric in PERCENT_METRICS and unit == "x":
            if c > 1 and round(c, nd) == value:  # "15x" in a coverage sentence
                return True, f"{token}x = threshold"
        elif metric == "median_depth" and unit in ("x", ""):
            if round(c, nd) == value:
                return True, f"{token} ≈ {c}"
        elif unit == "" and metric not in PERCENT_METRICS:
            if round(c, nd) == value:
                return True, f"{token} ≈ {c}"
    return False, f"{token}{unit.strip()} does not match {fact}"


def _status_claim(sentence: str) -> Optional[str]:
    ok, low, high = bool(_OK_RE.search(sentence)), bool(_LOW_RE.search(sentence)), bool(_HIGH_RE.search(sentence))
    out, na = bool(_OUT_RE.search(sentence)), bool(_NA_RE.search(sentence))
    classes = [c for c, hit in (("OK", ok), ("LOW", low), ("HIGH", high), ("OUT", out), ("NA", na)) if hit]
    if not classes:
        return None
    if "OUT" in classes and set(classes) - {"OUT", "LOW", "HIGH"}:
        return "CONFLICT"
    if len(set(classes) - {"OUT"}) > 1:
        return "CONFLICT"
    return (set(classes) - {"OUT"}).pop() if set(classes) - {"OUT"} else "OUT"


def verify_sentence(sentence: str, facts: Dict[str, Any], gene_symbols: Sequence[str]) -> SentenceVerdict:
    v = SentenceVerdict(sentence=sentence, verified=False)
    if not sentence.rstrip().endswith((".", ";", "!", "?")):
        v.reasons.append("incomplete sentence")
        return v
    if _FORBIDDEN_RE.search(sentence):
        v.reasons.append("clinical advice or claim outside the statistics (therapy, prognosis, screening…)")
        return v
    if _NEGATION_RE.search(_ALLOWED_NEGATIONS.sub(" ", sentence)):
        v.reasons.append("negation outside the fixed phrases (unverifiable)")
        return v

    mentions = _mentions(sentence)
    metrics = sorted({m[0] for m in mentions}, key=lambda k: list(_METRIC_PATTERNS).index(k))

    # Numbers → metric of the nearest mention
    for m in _NUMBER_RE.finditer(sentence):
        token, unit = m.group(1), (m.group(2) or "")
        if not mentions:
            v.reasons.append(f"number {token}{unit.strip()} not attached to any metric")
            continue
        centre = (m.start() + m.end()) / 2
        metric = min(mentions, key=lambda t: min(abs(centre - t[1]), abs(centre - t[2])))[0]
        ok, detail = _number_ok(metric, token, unit, facts)
        v.claims.append({"type": "number", "metric": metric, "value": token + unit.strip(), "ok": ok, "detail": detail})
        if not ok:
            v.reasons.append(f"{metric}: {detail}")

    # Status words → every status-bearing metric of the sentence
    status_metrics = [m for m in metrics if m in STATUS_METRICS]
    claim = _status_claim(sentence)
    if claim == "CONFLICT":
        v.reasons.append("contradictory status words")
    elif claim is not None:
        if not status_metrics:
            v.reasons.append(f"status '{claim}' not attached to any metric")
        for metric in status_metrics:
            actual = facts["status"][metric]
            ok = actual in (LOW, HIGH) if claim == "OUT" else actual == claim
            v.claims.append({"type": "status", "metric": metric, "claimed": claim, "actual": actual, "ok": ok})
            if not ok:
                v.reasons.append(f"{metric}: status '{claim}' but computed status is {actual}")

    # Risk level
    risk_claims = {next(g for g in m.groups() if g).upper() for m in _RISK_RE.finditer(sentence)}
    for r in sorted(risk_claims):
        ok = r == facts["risk_level"]
        v.claims.append({"type": "risk", "claimed": r, "actual": facts["risk_level"], "ok": ok})
        if not ok:
            v.reasons.append(f"risk {r} but the rules give {facts['risk_level']}")

    # Genes and their role
    genes = sorted({g for g in gene_symbols if re.search(rf"\b{re.escape(g)}\b", sentence)})
    none_confirmed = bool(_NONE_CONFIRMED_RE.search(sentence))
    roles = [r for r, rx in (("confirmed", _CONFIRMED_RE), ("to_confirm", _TO_CONFIRM_RE), ("not_excluded", _NOT_EXCLUDED_RE)) if rx.search(sentence)]
    if none_confirmed:
        roles = [r for r in roles if r != "confirmed"]
        ok = not facts["confirmed"] and not genes
        v.claims.append({"type": "none_confirmed", "ok": ok})
        if not ok:
            v.reasons.append("states no confirmed P/LP variant, but the analysis confirmed one" if facts["confirmed"] else "gene named in a 'no variant' sentence")
    if len(roles) > 1:
        v.reasons.append("several gene roles in one sentence (ambiguous)")
    elif genes:
        role = roles[0] if roles else None
        if role is None:
            v.reasons.append(f"gene(s) {', '.join(genes)} named without a verifiable role")
        else:
            expected = set(facts["genes_not_excluded" if role == "not_excluded" else role])
            ok = set(genes) <= expected
            v.claims.append({"type": "genes", "role": role, "genes": genes, "ok": ok, "complete": set(genes) == expected})
            if not ok:
                v.reasons.append(f"{role}: {', '.join(sorted(set(genes) - expected))} not in the analysis")
    elif roles and not none_confirmed:
        v.reasons.append(f"role '{roles[0]}' stated without a gene")

    if not v.claims:
        v.reasons.append("no verifiable claim")
    v.verified = not v.reasons
    if v.verified:
        v.topics = _covered_topics(v, facts)
    return v


def _covered_topics(v: SentenceVerdict, facts: Dict[str, Any]) -> List[str]:
    """Topics a verified sentence fully answers (value or status stated, gene lists complete)."""
    topics = []
    numbers = {c["metric"] for c in v.claims if c["type"] == "number" and c["ok"] and c["detail"].find("threshold") < 0}
    statuses = {c["metric"] for c in v.claims if c["type"] == "status"}
    if "variants" in numbers:
        topics.append("overview")
    for metric in STATUS_METRICS:
        if metric in statuses:
            topics.append(metric)
    if "low_vaf" in numbers:
        topics.append("low_vaf")
    for c in v.claims:
        if c["type"] == "genes" and c["complete"]:
            topics.append(c["role"])
        elif c["type"] == "none_confirmed":
            topics.append("confirmed")
        elif c["type"] == "risk":
            topics.append("risk")
    return [t for t in TOPICS if t in topics]


def verify_interpretation(text: str, facts: Dict[str, Any], gene_symbols: Sequence[str]) -> List[SentenceVerdict]:
    seen, verdicts = set(), []
    for s in split_sentences(text):
        key = s.lower()
        if key in seen:
            continue  # repeated sentence (degenerate generation): counted once
        seen.add(key)
        verdicts.append(verify_sentence(s, facts, gene_symbols))
    return verdicts


# --- 5. Final answer -----------------------------------------------------------------
def assemble_final(model_text: Optional[str], facts: Dict[str, Any], gene_symbols: Sequence[str]) -> Dict[str, Any]:
    """Final interpretation: verified model sentence per topic, else the reference sentence."""
    verdicts = verify_interpretation(normalize_generated_text(model_text or ""), facts, gene_symbols)
    required = required_topics(facts)
    reference = dict(reference_sentences(facts))
    chosen: Dict[str, Tuple[str, str]] = {}  # topic → (sentence, source)
    used_sentences = set()
    for v in verdicts:
        if not v.verified:
            continue
        new = [t for t in v.topics if t in required and t not in chosen]
        if not new:
            continue
        for t in new:
            chosen[t] = (v.sentence, "model")
        used_sentences.add(v.sentence)
    for t in required:
        chosen.setdefault(t, (reference[t], "reference"))

    parts, emitted, per_topic = [], set(), []
    for t in required:
        sentence, source = chosen[t]
        per_topic.append({"topic": t, "source": source, "sentence": sentence})
        if sentence not in emitted:
            parts.append(sentence)
            emitted.add(sentence)
    final_text = " ".join(parts)

    # Final re-verification: every sentence of the answer must pass (guaranteed by construction)
    final_verdicts = verify_interpretation(final_text, facts, gene_symbols)
    generated = len(verdicts)
    verified = sum(1 for v in verdicts if v.verified)
    return {
        "text": final_text,
        "per_topic": per_topic,
        "final_verified": all(v.verified for v in final_verdicts),
        "metrics": {
            "generated_sentences": generated,
            "verified_sentences": verified,
            "rejected_sentences": generated - verified,
            "required_topics": len(required),
            "topics_from_model": sum(1 for p in per_topic if p["source"] == "model"),
            "topics_from_reference": sum(1 for p in per_topic if p["source"] == "reference"),
        },
        "sentences": [v.to_dict() for v in verdicts],
        "versions": {"facts": FACTS_VERSION, "reference": REFERENCE_VERSION, "verifier": VERIFIER_VERSION},
    }
