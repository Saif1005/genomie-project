"""BioGPT evaluation (base model vs LoRA adapter): perplexity and factual accuracy.

Two families of metrics, computed deterministically (greedy decoding, frozen data):

1. Perplexity on the PubMed abstracts of the test split (never seen in training).
2. Factual accuracy of completions for the 13 genes × several prompts. The "in_distribution"
   prompt is the production one; the "held_out" prompts are absent from the training corpus and
   measure generalisation. Each completion (first sentence) is judged by `verify_text` against the
   reference gene–disease associations.

Promotion criterion (`acceptance`): the adapter is only used in production if it improves
perplexity WITHOUT degrading factual accuracy (verified rate ≥ base, unsupported-claim rate ≤ base).
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Sequence

from src.llm.knowledge import CORE_ASSOCIATIONS, PANEL_GENES, GeneDiseaseKnowledge, verify_text

EVAL_PROMPTS: Dict[str, Sequence[str]] = {
    "in_distribution": ("Germline pathogenic variants in {gene} are associated with",),
    "held_out": (
        "Women who carry a pathogenic {gene} mutation are at increased risk of",
        "Individuals with a pathogenic germline variant in {gene} have an elevated risk of",
        "The main cancers observed in families with inherited {gene} mutations are",
    ),
}

_SENTENCE_END = re.compile(r"(?<=[.;])\s")


def first_sentence(text: str) -> str:
    """First complete sentence (the one completing the prompt): this is what gets verified."""
    return _SENTENCE_END.split(text.strip(), maxsplit=1)[0] if text else ""


def perplexity(model, tokenizer, texts: Sequence[str], device: str, block: int = 512) -> Dict[str, float]:
    """Per-token perplexity over contiguous blocks (documents separated by </s>)."""
    import torch

    ids: List[int] = []
    for t in texts:
        ids += tokenizer(t, add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
    nll, n_tokens = 0.0, 0
    model.eval()
    with torch.no_grad():
        for i in range(0, len(ids) - 1, block):
            chunk = torch.tensor([ids[i:i + block + 1]], device=device)
            if chunk.shape[1] < 2:
                break
            logits = model(input_ids=chunk[:, :-1]).logits.float()
            loss = torch.nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)), chunk[:, 1:].reshape(-1), reduction="sum")
            nll += loss.item()
            n_tokens += chunk.shape[1] - 1
    return {"perplexity": round(math.exp(nll / n_tokens), 3), "tokens": n_tokens}


def _distinct_2(texts: Sequence[str]) -> float:
    grams, total = set(), 0
    for t in texts:
        w = t.lower().split()
        pairs = list(zip(w, w[1:]))
        grams.update(pairs)
        total += len(pairs)
    return round(len(grams) / total, 4) if total else 0.0


def factual_evaluation(generate, knowledge: GeneDiseaseKnowledge, genes: Sequence[str] = PANEL_GENES) -> Dict:
    """`generate(prompt) -> text`; returns metrics per prompt family and the details."""
    return _score(((family, tpl.format(gene=gene), gene, first_sentence(generate(tpl.format(gene=gene))))
                   for family, prompts in EVAL_PROMPTS.items() for tpl in prompts for gene in genes), knowledge)


def rescore(evaluation_details: Sequence[Dict], knowledge: GeneDiseaseKnowledge) -> Dict:
    """Re-judges stored generations (auditable evaluation without GPU or regeneration)."""
    return _score(((d["family"], d["prompt"], d["gene"], d["text"]) for d in evaluation_details), knowledge)


def _score(items, knowledge: GeneDiseaseKnowledge) -> Dict:
    results: Dict[str, Dict] = {}
    details = []
    by_family: Dict[str, List] = {}
    for family, prompt, gene, sentence in items:
        by_family.setdefault(family, []).append((prompt, gene, sentence))
    for family, rows in by_family.items():
        verdicts = []
        for prompt, gene, sentence in rows:
            v = verify_text(gene, sentence, knowledge)
            core = CORE_ASSOCIATIONS.get(gene, ())
            coverage = len(set(v.diseases) & set(core)) / len(core) if core else 0.0
            verdicts.append((v, coverage))
            details.append({"family": family, "prompt": prompt, "coverage_core": round(coverage, 3), **v.to_dict()})
        n = len(verdicts)
        results[family] = {
            "generations": n,
            "verified_rate": round(sum(v.verified for v, _ in verdicts) / n, 4),
            "unsupported_claim_rate": round(sum(bool(v.unsupported) for v, _ in verdicts) / n, 4),
            "no_specific_claim_rate": round(sum(v.reason is not None and v.reason.startswith("no specific") for v, _ in verdicts) / n, 4),
            "other_gene_rate": round(sum(bool(v.other_genes) for v, _ in verdicts) / n, 4),
            "empty_rate": round(sum(not v.text for v, _ in verdicts) / n, 4),
            "mean_core_coverage": round(sum(c for _, c in verdicts) / n, 4),
            "distinct_2": _distinct_2([v.text for v, _ in verdicts]),
        }
    return {"metrics": results, "details": details}


def acceptance(base: Dict, tuned: Dict) -> Dict:
    """Adapter promotion criterion (every condition must hold)."""
    checks = {
        "perplexity_improved": tuned["perplexity"]["perplexity"] < base["perplexity"]["perplexity"],
    }
    for family in EVAL_PROMPTS:
        b, t = base["factual"]["metrics"][family], tuned["factual"]["metrics"][family]
        checks[f"{family}_verified_not_worse"] = t["verified_rate"] >= b["verified_rate"]
        checks[f"{family}_unsupported_not_worse"] = t["unsupported_claim_rate"] <= b["unsupported_claim_rate"]
    return {"accepted": all(checks.values()), "checks": checks}
