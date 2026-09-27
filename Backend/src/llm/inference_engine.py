"""BioGPT — optional literature commentary, never used for decisions.

BioGPT (microsoft/biogpt) is a completion model trained on PubMed abstracts: it does not follow
instructions and cannot produce a reliable risk level. Risk is therefore computed by
src.genomics.risk; BioGPT only completes seed sentences such as
"Germline pathogenic variants in BRCA1 are associated with …", and every sentence is verified.

Greedy decoding (do_sample=False): same model, same seed sentence → same text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from config.settings import biogpt

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    HAS_TORCH = True
except ImportError:  # pragma: no cover - depends on the image
    HAS_TORCH = False
    torch = None  # type: ignore[assignment]


class InferenceError(RuntimeError):
    pass


GENERATION_CONFIG = {
    "max_new_tokens": 80,
    "do_sample": False,
    "num_beams": 1,
    "repetition_penalty": 1.3,
    "no_repeat_ngram_size": 3,
}


def generate_completion(model, tokenizer, prompt: str, device: str, max_new_tokens: int = 80) -> str:
    """Fixed greedy decoding (production and evaluation share exactly these parameters)."""
    max_pos = getattr(model.config, "max_position_embeddings", 1024)
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=max_pos - max_new_tokens).to(device)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            **{**GENERATION_CONFIG, "max_new_tokens": max_new_tokens},
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return _clean(f"{prompt} {generated}")


def commentary_prompts(genes: List[str]) -> List[str]:
    return [f"Germline pathogenic variants in {g} are associated with" for g in genes]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Cut at the last complete sentence: no truncated fragment in a report
    end = max(text.rfind("."), text.rfind(";"))
    return text[: end + 1] if end > 20 else ""


class BioGPTCommentator:
    def __init__(self, base_model: Optional[str] = None, adapter_path: Optional[str] = None, device: Optional[str] = None):
        cfg = biogpt()
        self.base_model = base_model or cfg.model
        self.adapter_path = adapter_path or cfg.adapter_path  # optional BioGPT LoRA adapter
        requested = (device or cfg.device).lower()
        if HAS_TORCH and requested in ("cuda", "auto"):
            if torch.cuda.is_available():
                self.device = "cuda"
            else:
                logger.warning("CUDA unavailable — BioGPT running on CPU (slower)")
                self.device = "cpu"
        else:
            self.device = "cpu"
        self.model = None
        self.tokenizer = None

    def load(self) -> None:
        if not HAS_TORCH:
            raise InferenceError("torch/transformers required for BioGPT")
        if self.model is not None:
            return
        torch.manual_seed(0)
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        model = AutoModelForCausalLM.from_pretrained(self.base_model, dtype=dtype).to(self.device)
        if self.adapter_path and Path(self.adapter_path).is_dir():
            from peft import PeftModel

            logger.info(f"LoRA adapter: {self.adapter_path}")
            model = PeftModel.from_pretrained(model, self.adapter_path).merge_and_unload()
        model.eval()
        self.model = model
        logger.info(f"BioGPT loaded ({self.base_model}, {self.device})")

    def unload(self) -> None:
        from src.utils.gpu_manager import get_gpu_manager

        gpu = get_gpu_manager()
        if self.model is not None:
            gpu.unload_hf_model(self.model)
        self.model = None
        self.tokenizer = None
        gpu.empty_cuda_cache()

    def complete(self, prompt: str, max_new_tokens: int = 80) -> str:
        self.load()
        return generate_completion(self.model, self.tokenizer, prompt, self.device, max_new_tokens)

    def comment_on_genes(self, genes: List[str]) -> str:
        sentences = [self.complete(p) for p in commentary_prompts(genes)]
        return " ".join(s for s in sentences if s)

    def verified_commentary(self, genes: List[str], knowledge) -> Tuple[str, Dict[str, Any]]:
        """Per-gene commentary: BioGPT sentence if verified (src.llm.knowledge), else the fallback sentence.

        No generated sentence enters the report without gene–disease verification.
        """
        from src.llm.knowledge import fallback_sentence, verify_text
        from src.llm.model_evaluator import first_sentence

        per_gene, parts = [], []
        for gene, prompt in zip(genes, commentary_prompts(genes)):
            sentence = first_sentence(self.complete(prompt))
            verdict = verify_text(gene, sentence, knowledge)
            used = sentence if verdict.verified else fallback_sentence(gene, knowledge)
            parts.append(used)
            per_gene.append({
                **verdict.to_dict(),
                "used": "biogpt" if verdict.verified else "reference",
                "final_text": used,
            })
        verification = {
            "method": "verify_text (lexicon + curated ClinGen/NCCN associations)",
            "knowledge_version": knowledge.version,
            "clinvar_version": knowledge.clinvar_version,
            "model": self.base_model,
            "adapter": self.adapter_path if self.adapter_path and Path(self.adapter_path).is_dir() else None,
            "generated": len(per_gene),
            "verified": sum(1 for g in per_gene if g["verified"]),
            "per_gene": per_gene,
        }
        return " ".join(parts), verification
