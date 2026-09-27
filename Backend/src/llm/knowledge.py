"""Reference gene–disease knowledge and verification of BioGPT-generated text.

BioGPT remains a completion model: it can "hallucinate" a gene–disease association. In a
clinical setting no generated sentence enters the report without verification:

1. `DISEASE_LEXICON` recognises the diseases mentioned in a text (fixed regular expressions).
2. `CURATED_ASSOCIATIONS`: established gene–disease associations for the 13 germline genes of the
   panel (ClinGen "definitive / strong" validity and NCCN Genetic/Familial High-Risk Assessment:
   Breast, Ovarian, Pancreatic and Prostate). Frozen, versioned table.
3. `clinvar_associations` counts, as displayed supporting evidence, the diseases cited in reviewed
   P/LP records (≥ 2 stars) of the local ClinVar release. These counts do NOT widen the accepted
   associations: CLNDN aggregates the conditions of every submitter (e.g. "Gastric cancer" added by
   multigene panels on BRCA1, RAD51D, TP53…), while the stars refer to the classification, not to
   each condition. Gene–disease validity is ClinGen's domain, hence the curated table as the only
   acceptance reference.
4. `verify_text`: a sentence citing a disease outside the curated table, another panel gene, or a
   claim the table cannot verify (comparison, number, somatic context, therapy, prognosis,
   negation) is rejected. `fallback_sentence` then produces a deterministic text from the table.

Everything is deterministic: same text, same version → same verdict.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

KNOWLEDGE_VERSION = "germlineiq-gene-disease-v2"  # v2: risk magnitude checked against penetrance
CLINVAR_MIN_RECORDS = 3
CLINVAR_MIN_STARS = 2

# Generic multigene-panel condition names: removed before searching CLNDN for diseases
_GENERIC_CONDITIONS = re.compile(
    r"hereditary breast (?:and )?ovarian cancer(?: syndrome)?|breast-ovarian cancer, familial[^|]*|"
    r"breast and/or ovarian cancer|hereditary cancer-predisposing syndrome|familial cancer of breast",
    re.IGNORECASE,
)

# Canonical id → (English label, regular expression)
DISEASE_LEXICON: Dict[str, Tuple[str, str]] = {
    "breast_cancer": ("breast cancer", r"\bbreast (?:cancer|carcinoma|tumou?r)s?\b|\bmammary carcinoma"),
    "ovarian_cancer": ("ovarian cancer", r"\bovar(?:ian|y) (?:cancer|carcinoma|tumou?r)s?\b|\btubo-?ovarian\b|\bfallopian tube (?:cancer|carcinoma)"),
    "pancreatic_cancer": ("pancreatic cancer", r"\bpancrea(?:tic|s) (?:cancer|carcinoma|adenocarcinoma|ductal adenocarcinoma)s?\b|\bPDAC\b"),
    "prostate_cancer": ("prostate cancer", r"\bprostat(?:e|ic) (?:cancer|carcinoma|adenocarcinoma)s?\b"),
    "fanconi_anemia": ("Fanconi anemia", r"\bfanconi an(?:a)?emia\b"),
    "li_fraumeni": ("Li-Fraumeni syndrome", r"\bli-?fraumeni\b"),
    "sarcoma": ("sarcoma", r"\b(?:osteo|rhabdomyo|lipo|leiomyo)?sarcomas?\b"),
    "brain_tumor": ("brain tumours", r"\b(?:brain tumou?rs?|glioma|glioblastoma|astrocytoma|medulloblastoma|choroid plexus carcinoma)s?\b"),
    "adrenocortical_carcinoma": ("adrenocortical carcinoma", r"\badrenocortical (?:carcinoma|cancer|tumou?r)s?\b"),
    "leukemia": ("leukaemia", r"\bleuka?emias?\b|\blymphomas?\b"),
    "cowden": ("PTEN hamartoma tumour syndrome", r"\bcowden\b|\bhamartoma tumou?r syndrome\b|\bPHTS\b|\bbannayan"),
    "thyroid_cancer": ("thyroid cancer", r"\bthyroid (?:cancer|carcinoma)s?\b"),
    "endometrial_cancer": ("endometrial cancer", r"\bendometri(?:al|um) (?:cancer|carcinoma)s?\b|\buterine (?:cancer|carcinoma)"),
    "kidney_cancer": ("kidney cancer", r"\b(?:kidney|renal cell) (?:cancer|carcinoma)s?\b"),
    "colorectal_cancer": ("colorectal cancer", r"\b(?:colorectal|colon|rectal) (?:cancer|carcinoma|adenocarcinoma)s?\b"),
    "gastric_cancer": ("gastric cancer", r"\b(?:gastric|stomach) (?:cancer|carcinoma|adenocarcinoma)s?\b|\bHDGC\b"),
    "lobular_breast_cancer": ("lobular breast cancer", r"\blobular (?:breast )?(?:cancer|carcinoma)s?\b"),
    "peutz_jeghers": ("Peutz-Jeghers syndrome", r"\bpeutz-?jeghers\b"),
    "gastrointestinal_cancer": ("gastrointestinal cancers", r"\b(?:gastrointestinal|small (?:bowel|intestin(?:e|al))) (?:cancer|carcinoma|tumou?r)s?\b|\bhamartomatous polyps?\b"),
    "cervical_cancer": ("cervical adenocarcinoma", r"\bcervi(?:cal|x) (?:cancer|adenocarcinoma|carcinoma)s?\b|\badenoma malignum\b"),
    "testicular_tumor": ("testicular tumours", r"\b(?:testicular|sertoli cell) (?:cancer|tumou?r)s?\b"),
    "neurofibromatosis": ("neurofibromatosis type 1", r"\bneurofibromatosis\b|\bNF-?1 syndrome\b|\bvon recklinghausen\b"),
    "mpnst": ("malignant peripheral nerve sheath tumour", r"\bmalignant peripheral nerve sheath tumou?rs?\b|\bMPNST\b"),
    "gist": ("gastrointestinal stromal tumour", r"\bgastrointestinal stromal tumou?rs?\b|\bGIST\b"),
    "pheochromocytoma": ("pheochromocytoma", r"\bph(?:a)?eochromocytomas?\b|\bparaganglioma"),
    "ataxia_telangiectasia": ("ataxia-telangiectasia", r"\bataxia[- ]telangiectasia\b"),
    "melanoma": ("melanoma", r"\bmelanomas?\b"),
    "neuroblastoma": ("neuroblastoma", r"\bneuroblastomas?\b"),
    "lynch": ("Lynch syndrome", r"\blynch syndrome\b|\bHNPCC\b"),
    "lung_cancer": ("lung cancer", r"\blung (?:cancer|carcinoma|adenocarcinoma)s?\b"),
    "liver_cancer": ("liver cancer", r"\b(?:liver|hepatocellular) (?:cancer|carcinoma)s?\b"),
    "bladder_cancer": ("bladder cancer", r"\b(?:bladder|urothelial) (?:cancer|carcinoma)s?\b"),
    "retinoblastoma": ("retinoblastoma", r"\bretinoblastomas?\b"),
    "wilms": ("Wilms tumour", r"\bwilms'? tumou?rs?\b|\bnephroblastoma"),
    "hbo_syndrome": ("hereditary breast and ovarian cancer syndrome", r"\bhereditary breast (?:and|-) ?ovarian cancer\b|\bHBOC\b"),
}

_COMPILED = {k: re.compile(v[1], re.IGNORECASE) for k, v in DISEASE_LEXICON.items()}

# Generic terms: "hereditary cancer" is not a specific, verifiable claim
GENERIC_TERMS = ("hbo_syndrome",)

# Established associations (ClinGen definitive/strong, NCCN 2025-2026) — germline panel genes.
# Trailing comments: inheritance or specific context.
CURATED_ASSOCIATIONS: Dict[str, Tuple[str, ...]] = {
    "ATM": ("breast_cancer", "pancreatic_cancer", "prostate_cancer", "ovarian_cancer", "ataxia_telangiectasia", "leukemia", "melanoma"),  # AT: biallelic
    "BARD1": ("breast_cancer", "ovarian_cancer", "neuroblastoma"),
    "BRCA1": ("breast_cancer", "ovarian_cancer", "hbo_syndrome", "pancreatic_cancer", "prostate_cancer", "fanconi_anemia"),  # FA: biallelic
    "BRCA2": ("breast_cancer", "ovarian_cancer", "hbo_syndrome", "pancreatic_cancer", "prostate_cancer", "melanoma", "fanconi_anemia", "brain_tumor", "wilms", "leukemia"),  # FA-D1: biallelic
    "CDH1": ("gastric_cancer", "lobular_breast_cancer", "breast_cancer", "colorectal_cancer"),
    "CHEK2": ("breast_cancer", "prostate_cancer", "colorectal_cancer", "kidney_cancer", "thyroid_cancer"),
    "NF1": ("neurofibromatosis", "breast_cancer", "mpnst", "brain_tumor", "gist", "pheochromocytoma", "leukemia"),
    "PALB2": ("breast_cancer", "pancreatic_cancer", "ovarian_cancer", "prostate_cancer", "fanconi_anemia"),
    "PTEN": ("cowden", "breast_cancer", "thyroid_cancer", "endometrial_cancer", "kidney_cancer", "colorectal_cancer", "melanoma"),
    "RAD51C": ("ovarian_cancer", "breast_cancer", "fanconi_anemia"),
    "RAD51D": ("ovarian_cancer", "breast_cancer"),
    "STK11": ("peutz_jeghers", "breast_cancer", "pancreatic_cancer", "gastrointestinal_cancer", "colorectal_cancer", "gastric_cancer", "ovarian_cancer", "cervical_cancer", "testicular_tumor", "lung_cancer"),
    "TP53": ("li_fraumeni", "breast_cancer", "sarcoma", "brain_tumor", "adrenocortical_carcinoma", "leukemia", "colorectal_cancer", "pancreatic_cancer", "lung_cancer", "liver_cancer"),
}

# Core associations cited in the fallback sentence (order = clinical importance)
CORE_ASSOCIATIONS: Dict[str, Tuple[str, ...]] = {
    "ATM": ("breast_cancer", "pancreatic_cancer", "prostate_cancer"),
    "BARD1": ("breast_cancer",),
    "BRCA1": ("breast_cancer", "ovarian_cancer", "pancreatic_cancer", "prostate_cancer"),
    "BRCA2": ("breast_cancer", "ovarian_cancer", "pancreatic_cancer", "prostate_cancer", "melanoma"),
    "CDH1": ("gastric_cancer", "lobular_breast_cancer"),
    "CHEK2": ("breast_cancer", "prostate_cancer", "colorectal_cancer"),
    "NF1": ("neurofibromatosis", "breast_cancer", "mpnst"),
    "PALB2": ("breast_cancer", "pancreatic_cancer", "ovarian_cancer"),
    "PTEN": ("cowden", "breast_cancer", "thyroid_cancer", "endometrial_cancer"),
    "RAD51C": ("ovarian_cancer", "breast_cancer"),
    "RAD51D": ("ovarian_cancer", "breast_cancer"),
    "STK11": ("peutz_jeghers", "breast_cancer", "pancreatic_cancer", "gastrointestinal_cancer"),
    "TP53": ("li_fraumeni", "breast_cancer", "sarcoma", "brain_tumor", "adrenocortical_carcinoma"),
}

PANEL_GENES = tuple(sorted(CURATED_ASSOCIATIONS))

# Breast cancer penetrance class (NCCN Genetic/Familial High-Risk Assessment), identical to the panel
# (data/cancer_genes/cancer_genes_db.json, checked by a unit test). A risk magnitude written by the
# model must match it: "high risk" for CHEK2 (moderate penetrance) is a factual error.
PENETRANCE: Dict[str, str] = {
    **{g: "high" for g in ("BRCA1", "BRCA2", "CDH1", "PALB2", "PTEN", "STK11", "TP53")},
    **{g: "moderate" for g in ("ATM", "BARD1", "CHEK2", "NF1", "RAD51C", "RAD51D")},
}
_MAGNITUDE = re.compile(
    r"\b(high|higher|highly|substantial|substantially|strong|strongly|very|markedly|"
    r"moderate|moderately|intermediate|low|lower|slight|slightly|small|modest|modestly)\b"
    r"(?:[- ](?:elevated|increased|raised))?[- ](?:lifetime )?(?:risks?|penetrance|susceptibility)\b",
    re.IGNORECASE,
)
_HIGH_WORDS = {"high", "higher", "highly", "substantial", "substantially", "strong", "strongly", "very", "markedly"}
_MODERATE_WORDS = {"moderate", "moderately", "intermediate"}


def magnitude_error(gene: str, text: str) -> Optional[str]:
    """Risk magnitude inconsistent with the gene's penetrance class, else None."""
    pen = PENETRANCE.get(gene)
    for m in _MAGNITUDE.finditer(text):
        word = m.group(1).lower()
        if word in _HIGH_WORDS and pen != "high":
            return f"'{m.group(0)}' but {gene} has {pen} penetrance"
        if word in _MODERATE_WORDS and pen != "moderate":
            return f"'{m.group(0)}' but {gene} has {pen} penetrance"
        if word not in _HIGH_WORDS | _MODERATE_WORDS:
            return f"'{m.group(0)}' understates a {pen}-penetrance gene"
    return None

# Claims the gene–disease table cannot verify: the whole sentence is rejected.
# (expert review: "higher risk of breast cancer than somatic mutations" passed v1)
UNVERIFIABLE_CLAIMS: Tuple[Tuple[str, str], ...] = (
    ("comparison", r"\b(?:than|compared (?:to|with)|versus|vs\.?)\b"),
    # standalone numbers (except "type 1", "group D1"); gene symbols are removed before the test
    ("number or quantified risk", r"(?<![\w])(?<!type )(?<!group )\d+(?:[.,]\d+)?|%|\b(?:percent|fold|odds ratio|hazard ratio|relative risk)\b"),
    ("somatic context", r"\bsomatic\b|\btumou?r (?:tissue|sample)s?\b"),
    ("therapy", r"\b(?:therap\w*|treat\w*|PARP|inhibitor\w*|chemotherap\w*|platinum|drug\w*|surgery|mastectomy|oophorectomy)\b"),
    ("prognosis", r"\b(?:survival|mortality|prognos\w*|outcome\w*|recurrence)\b"),
    ("negation", r"\b(?:not|no|neither|nor|without)\b"),
    ("unassessed phenotype", r"\b(?:intellectual disability|autism|schizophren\w*|diabetes|cardio\w*)\b"),
)
_UNVERIFIABLE = tuple((label, re.compile(rx, re.IGNORECASE)) for label, rx in UNVERIFIABLE_CLAIMS)
_GENE_RE = {g: re.compile(rf"\b{g}\b") for g in PANEL_GENES}


# Coordinated lists: "breast, ovarian and endometrial cancers" → each organ gets the head noun
# ("cancers"), otherwise "breast" and "ovarian" would escape verification.
_ORGANS = r"(?:breast|ovarian|pancreatic|prostate|prostatic|endometrial|thyroid|colorectal|colon|rectal|gastric|stomach|kidney|renal|lung|liver|bladder|cervical|brain|skin)"
_HEAD = r"(?:cancers?|carcinomas?|adenocarcinomas?|tumou?rs?)"
_COORD = re.compile(rf"\b({_ORGANS}(?:(?:\s*,\s*|\s+(?:and|or|and/or)\s+|\s*,\s*(?:and|or)\s+){_ORGANS})+)\s+({_HEAD})\b", re.IGNORECASE)
_SPLIT_COORD = re.compile(r"\s*,\s*(?:and|or)\s+|\s*,\s*|\s+(?:and/or|and|or)\s+", re.IGNORECASE)


def _expand_coordination(text: str) -> str:
    def repl(m: re.Match) -> str:
        organs = [o for o in _SPLIT_COORD.split(m.group(1)) if o]
        return " ; ".join(f"{o} {m.group(2)}" for o in organs)
    return _COORD.sub(repl, text)


def find_diseases(text: str) -> List[str]:
    """Lexicon diseases cited in the text (sorted ids), coordinated lists included."""
    expanded = _expand_coordination(text or "")
    return sorted(k for k, rx in _COMPILED.items() if rx.search(expanded))


def find_genes(text: str) -> List[str]:
    return sorted(g for g, rx in _GENE_RE.items() if rx.search(text or ""))


def clinvar_associations(
    records: Iterable[Tuple[Optional[str], Optional[str], int]],
    min_records: int = CLINVAR_MIN_RECORDS,
    min_stars: int = CLINVAR_MIN_STARS,
) -> Dict[str, Dict[str, int]]:
    """(gene, CLNDN, stars) of P/LP records → {gene: {disease: count}} (≥ min_records)."""
    counts: Dict[str, Counter] = {}
    for gene, conditions, stars in records:
        if not gene or not conditions or stars < min_stars:
            continue
        specific = _GENERIC_CONDITIONS.sub(" ", conditions.replace("_", " "))
        for d in find_diseases(specific):
            counts.setdefault(gene, Counter())[d] += 1
    return {
        g: {d: n for d, n in sorted(c.items()) if n >= min_records}
        for g, c in sorted(counts.items())
    }


@dataclass
class GeneDiseaseKnowledge:
    curated: Dict[str, Tuple[str, ...]] = field(default_factory=lambda: dict(CURATED_ASSOCIATIONS))
    clinvar: Dict[str, Dict[str, int]] = field(default_factory=dict)
    clinvar_version: Optional[str] = None
    version: str = KNOWLEDGE_VERSION

    def supported(self, gene: str) -> Set[str]:
        """Accepted associations: curated table only (ClinVar = supporting evidence, see header)."""
        return set(self.curated.get(gene, ()))

    def provenance(self, gene: str, disease: str) -> List[str]:
        src = []
        if disease in self.curated.get(gene, ()):
            src.append("curated")
        if disease in self.clinvar.get(gene, {}):
            src.append(f"clinvar:{self.clinvar[gene][disease]}")
        return src

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "clinvar_version": self.clinvar_version,
            "clinvar_min_records": CLINVAR_MIN_RECORDS,
            "clinvar_min_stars": CLINVAR_MIN_STARS,
            "curated": {g: list(v) for g, v in sorted(self.curated.items())},
            "clinvar": self.clinvar,
        }

    @classmethod
    def from_clinvar_index(cls, index) -> "GeneDiseaseKnowledge":
        """Local ClinVar release (ClinVarIndex) → enriched, versioned knowledge."""
        records = ((rec.gene, rec.conditions, rec.stars) for _k, rec in index.items() if rec.is_pathogenic)
        return cls(clinvar=clinvar_associations(records), clinvar_version=index.version)


@dataclass(frozen=True)
class Verdict:
    gene: str
    text: str
    verified: bool
    diseases: Tuple[str, ...]
    unsupported: Tuple[str, ...]
    other_genes: Tuple[str, ...]
    reason: Optional[str]

    def to_dict(self) -> Dict:
        return {
            "gene": self.gene,
            "text": self.text,
            "verified": self.verified,
            "diseases": list(self.diseases),
            "unsupported_diseases": list(self.unsupported),
            "other_panel_genes": list(self.other_genes),
            "reason": self.reason,
        }


def verify_text(gene: str, text: str, knowledge: GeneDiseaseKnowledge) -> Verdict:
    """Accept the text only if it cites ≥ 1 specific disease, all supported for this gene."""
    diseases = tuple(find_diseases(text))
    specific = tuple(d for d in diseases if d not in GENERIC_TERMS)
    unsupported = tuple(d for d in diseases if d not in knowledge.supported(gene))
    others = tuple(g for g in find_genes(text) if g != gene)
    without_genes = re.sub(r"\b[A-Z][A-Z0-9]{1,9}\b", " ", text)  # BRCA1, RAD51C… are not numbers
    unverifiable = [label for label, rx in _UNVERIFIABLE if rx.search(without_genes)]
    magnitude = magnitude_error(gene, text)
    if not text.strip():
        reason = "empty text"
    elif unverifiable:
        reason = "unverifiable claim: " + ", ".join(unverifiable)
    elif magnitude:
        reason = "risk magnitude inconsistent with penetrance: " + magnitude
    elif unsupported:
        reason = "unsupported association: " + ", ".join(DISEASE_LEXICON[d][0] for d in unsupported)
    elif others:
        reason = "mentions another panel gene: " + ", ".join(others)
    elif not specific:
        reason = "no specific disease mentioned (not verifiable)"
    else:
        reason = None
    return Verdict(gene, text, reason is None, diseases, unsupported, others, reason)


def _join_en(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def fallback_sentence(gene: str, knowledge: GeneDiseaseKnowledge) -> str:
    """Deterministic sentence built from the gene's core reference associations."""
    core = [d for d in CORE_ASSOCIATIONS.get(gene, ()) if d in knowledge.supported(gene)]
    if not core:
        return f"No reference gene–disease association is recorded for {gene}."
    labels = [DISEASE_LEXICON[d][0] for d in core]
    return (
        f"Germline pathogenic variants in {gene} are associated with an increased risk of {_join_en(labels)} "
        f"(ClinGen/NCCN reference associations, {knowledge.version})."
    )
