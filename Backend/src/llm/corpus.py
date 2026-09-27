"""BioGPT adaptation corpus: PubMed abstracts for the panel genes + reference gene–disease statements.

Sources (public):
- PubMed (NCBI E-utilities): for each germline panel gene, evidence syntheses (reviews,
  meta-analyses, guidelines) and primary studies on germline risk;
- `CURATED_ASSOCIATIONS` (src.llm.knowledge): validated gene–disease statements, repeated to anchor
  the reference associations in the model (knowledge injection).

Reproducibility: PubMed search results change over time, so the downloaded corpus is frozen
(`pubmed_abstracts.jsonl` sorted by PMID) with a manifest: queries, retrieval date, PMIDs,
SHA-256. The train/val/test split depends only on the SHA-256 of the document id (no random
draw): same corpus → same splits, on any machine.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from loguru import logger

from src.llm.knowledge import CORE_ASSOCIATIONS, CURATED_ASSOCIATIONS, DISEASE_LEXICON, GENERIC_TERMS, PANEL_GENES

CORPUS_VERSION = "germlineiq-biogpt-corpus-v3"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MIN_DATE, MAX_DATE = "2005/01/01", "2026/06/30"
MIN_ABSTRACT_CHARS = 400
FACT_REPEATS = 12
SPLIT_VAL, SPLIT_TEST = 90, 95  # SHA-256 mod 100: < 90 train, < 95 val, else test

# Per-gene queries: germline risk, cancers, abstract available, English, humans
_BASE = '("{gene}"[tiab]) AND (germline[tiab] OR hereditary[tiab] OR inherited[tiab] OR carrier*[tiab]) AND (cancer[tiab] OR carcinoma[tiab] OR tumor*[tiab] OR tumour*[tiab] OR syndrome[tiab]) AND hasabstract AND english[la] AND humans[mh]'
QUERIES = {
    "synthesis": _BASE + " AND (review[pt] OR meta-analysis[pt] OR systematic review[pt] OR guideline[pt] OR practice guideline[pt])",
    "primary": _BASE + " NOT (review[pt] OR meta-analysis[pt] OR systematic review[pt] OR comment[pt] OR editorial[pt] OR letter[pt] OR case reports[pt])",
}
RETMAX = {"synthesis": 120, "primary": 150}


# --- PubMed -------------------------------------------------------------------------
def _get(url: str, params: Dict[str, str], retries: int = 4) -> bytes:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=60) as r:
                data = r.read()
            time.sleep(0.4)  # ≤ 3 requests/s without an API key (NCBI rule)
            return data
        except OSError as e:
            wait = 2 ** attempt
            logger.warning(f"E-utilities: {e} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"E-utilities unreachable: {url}")


def esearch(term: str, retmax: int) -> List[str]:
    data = json.loads(_get(f"{EUTILS}/esearch.fcgi", {
        "db": "pubmed", "term": term, "retmax": str(retmax), "retmode": "json", "sort": "relevance",
        "datetype": "pdat", "mindate": MIN_DATE, "maxdate": MAX_DATE, "tool": "germlineiq",
    }))
    return data["esearchresult"]["idlist"]


def _text(el: Optional[ET.Element]) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_pubmed_xml(xml: bytes) -> List[Dict]:
    out = []
    for art in ET.fromstring(xml).findall(".//PubmedArticle"):
        pmid = _text(art.find(".//PMID"))
        parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label, txt = ab.get("Label"), _text(ab)
            if txt:
                parts.append(f"{label.capitalize()}: {txt}" if label else txt)
        year = _text(art.find(".//PubDate/Year")) or _text(art.find(".//PubDate/MedlineDate"))[:4]
        out.append({
            "pmid": pmid,
            "title": _text(art.find(".//ArticleTitle")),
            "abstract": " ".join(parts),
            "journal": _text(art.find(".//Journal/Title")),
            "year": year,
            "publication_types": sorted(_text(p) for p in art.findall(".//PublicationType")),
        })
    return out


def efetch(pmids: Sequence[str], batch: int = 150) -> List[Dict]:
    records = []
    for i in range(0, len(pmids), batch):
        xml = _get(f"{EUTILS}/efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids[i:i + batch]), "retmode": "xml", "tool": "germlineiq"})
        records += parse_pubmed_xml(xml)
    return records


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_pubmed(out_dir: Path, genes: Sequence[str] = PANEL_GENES) -> Path:
    """Downloads and freezes the abstracts; re-running reuses the existing corpus (reproducibility)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus, manifest_path = out_dir / "pubmed_abstracts.jsonl", out_dir / "pubmed_manifest.json"
    if corpus.is_file() and manifest_path.is_file():
        logger.info(f"Reusing frozen PubMed corpus: {corpus}")
        return corpus

    hits: Dict[str, Dict] = {}  # pmid → {genes, query_types}
    for gene in genes:
        for qtype, template in QUERIES.items():
            for pmid in esearch(template.format(gene=gene), RETMAX[qtype]):
                h = hits.setdefault(pmid, {"genes": set(), "query_types": set()})
                h["genes"].add(gene)
                h["query_types"].add(qtype)
        logger.info(f"PubMed {gene} : {sum(1 for h in hits.values() if gene in h['genes'])} PMIDs")

    pmids = sorted(hits, key=int)
    records = {r["pmid"]: r for r in efetch(pmids)}
    kept = []
    for pmid in pmids:
        r = records.get(pmid)
        if not r or len(r["abstract"]) < MIN_ABSTRACT_CHARS:
            continue
        r["genes"] = sorted(hits[pmid]["genes"])
        r["query_types"] = sorted(hits[pmid]["query_types"])
        kept.append(r)
    with open(corpus, "w", encoding="utf-8") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = {
        "version": CORPUS_VERSION,
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "NCBI PubMed E-utilities (esearch sort=relevance, efetch XML)",
        "date_range": [MIN_DATE, MAX_DATE],
        "queries": QUERIES,
        "retmax": RETMAX,
        "min_abstract_chars": MIN_ABSTRACT_CHARS,
        "pmids_found": len(pmids),
        "abstracts_kept": len(kept),
        "per_gene": {g: sum(1 for r in kept if g in r["genes"]) for g in genes},
        "sha256": sha256_file(corpus),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    logger.info(f"PubMed corpus: {len(kept)} abstracts kept out of {len(pmids)} PMIDs")
    return corpus


# --- Reference statements --------------------------------------------------------------
def _join_en(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# Training phrasings. The held-out evaluation prompts are different
# (src.llm.model_evaluator.EVAL_PROMPTS) to measure generalisation, not memorisation.
# v2: varied phrasings, each with the gene's own disease list, to counter the generic
# "breast, ovarian and …" pattern learnt from the HBOC literature (hallucinations measured in v1).
FACT_TEMPLATES = (
    "Germline pathogenic variants in {gene} are associated with {core}.",
    "Carriers of germline pathogenic {gene} variants have an increased lifetime risk of {core}.",
    "{gene} is an established cancer predisposition gene: pathogenic germline variants confer an increased risk of {full}.",
    "Pathogenic {gene} variants predispose carriers to {core}.",
    "The tumour spectrum of {gene} carriers includes {core}.",
    "Cancer risks associated with germline {gene} variants include {core}.",
    "In {gene} carriers, the established cancer associations are limited to {full}.",
    "Clinical management of {gene} carriers focuses on {core}.",
)

BIALLELIC_NOTES = {
    "BRCA1": "Biallelic pathogenic variants in BRCA1 cause Fanconi anemia.",
    "BRCA2": "Biallelic pathogenic variants in BRCA2 cause Fanconi anemia complementation group D1.",
    "PALB2": "Biallelic pathogenic variants in PALB2 cause Fanconi anemia complementation group N.",
    "RAD51C": "Biallelic pathogenic variants in RAD51C cause Fanconi anemia complementation group O.",
    "ATM": "Biallelic pathogenic variants in ATM cause ataxia-telangiectasia.",
}


def reference_statements(genes: Sequence[str] = PANEL_GENES) -> List[Dict]:
    docs = []
    for gene in genes:
        core = [DISEASE_LEXICON[d][0] for d in CORE_ASSOCIATIONS[gene]]
        full = [DISEASE_LEXICON[d][0] for d in CURATED_ASSOCIATIONS[gene] if d not in GENERIC_TERMS]
        for i, tpl in enumerate(FACT_TEMPLATES):
            docs.append({"id": f"fact:{gene}:{i}", "gene": gene, "source": "curated",
                         "text": tpl.format(gene=gene, core=_join_en(core), full=_join_en(full))})
        if gene in BIALLELIC_NOTES:
            docs.append({"id": f"fact:{gene}:biallelic", "gene": gene, "source": "curated", "text": BIALLELIC_NOTES[gene]})
    return docs


# --- Dataset ---------------------------------------------------------------------------
def split_of(doc_id: str) -> str:
    bucket = int(hashlib.sha256(doc_id.encode()).hexdigest(), 16) % 100
    return "train" if bucket < SPLIT_VAL else "val" if bucket < SPLIT_TEST else "test"


_PANEL_GENE_RE = re.compile(r"\b(?:" + "|".join(PANEL_GENES) + r")\b")
MAX_PANEL_GENES_CITED = 3


def gene_centric_genes(r: Dict) -> List[str]:
    """Genes the abstract is really about (v3): named in the title, or cited ≥ 2 times in an abstract
    citing at most 3 panel genes. Excludes multigene-panel studies, which teach the model the
    generic "breast, ovarian and …" list with no gene-specific link."""
    title, abstract = r["title"], r["abstract"]
    cited = set(_PANEL_GENE_RE.findall(abstract))
    keep = []
    for g in r["genes"]:
        rx = re.compile(rf"\b{g}\b")
        if rx.search(title) or (len(rx.findall(abstract)) >= 2 and len(cited) <= MAX_PANEL_GENES_CITED):
            keep.append(g)
    return keep


def abstract_text(r: Dict) -> str:
    return f"{r['title']} {r['abstract']}".strip()


def build_dataset(pubmed_jsonl: Path, out_dir: Path) -> Dict:
    """train/val/test.jsonl: abstracts split by hash; reference statements in train (× FACT_REPEATS)."""
    abstracts = [json.loads(l) for l in pubmed_jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    splits: Dict[str, List[Dict]] = {"train": [], "val": [], "test": []}
    excluded = 0
    for r in abstracts:
        genes = gene_centric_genes(r)
        if not genes:
            excluded += 1
            continue
        doc_id = f"pmid:{r['pmid']}"
        splits[split_of(doc_id)].append({"id": doc_id, "genes": genes, "source": "pubmed", "text": abstract_text(r)})
    facts = reference_statements()
    for k in range(FACT_REPEATS):
        for f in facts:
            splits["train"].append({**f, "id": f"{f['id']}#{k}"})

    stats = {}
    for name, docs in splits.items():
        docs.sort(key=lambda d: d["id"])
        path = out_dir / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for d in docs:
                fh.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")
        stats[name] = {
            "documents": len(docs),
            "pubmed": sum(1 for d in docs if d["source"] == "pubmed"),
            "reference_statements": sum(1 for d in docs if d["source"] == "curated"),
            "characters": sum(len(d["text"]) for d in docs),
            "sha256": sha256_file(path),
        }
    manifest = {
        "version": CORPUS_VERSION,
        "pubmed_sha256": sha256_file(pubmed_jsonl),
        "split_rule": f"sha256(id) % 100: < {SPLIT_VAL} train, < {SPLIT_TEST} val, else test",
        "fact_repeats": FACT_REPEATS,
        "gene_centric_filter": f"gene in the title, or cited ≥ 2 times with ≤ {MAX_PANEL_GENES_CITED} panel genes cited",
        "abstracts_total": len(abstracts),
        "abstracts_excluded_not_gene_centric": excluded,
        "per_gene_kept": {g: sum(1 for docs in splits.values() for d in docs if d["source"] == "pubmed" and g in d["genes"]) for g in PANEL_GENES},
        "splits": stats,
    }
    (out_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return manifest


def load_texts(path: Path, sources: Optional[Iterable[str]] = None) -> List[str]:
    keep = set(sources) if sources else None
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            if keep is None or d["source"] in keep:
                out.append(d["text"])
    return out
