"""BioGPT — commentaire bibliographique optionnel, jamais décisionnel.

BioGPT (microsoft/biogpt) est un modèle de complétion entraîné sur des résumés PubMed : il
n'obéit pas à des consignes et ne peut pas produire un niveau de risque fiable. Le risque est
donc calculé par src.genomics.risk ; BioGPT complète seulement des phrases d'amorce du type
« Germline pathogenic variants in BRCA1 are associated with … ».

Décodage glouton (do_sample=False) : même modèle, même amorce → même texte.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from loguru import logger

from config.settings import biogpt

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    HAS_TORCH = True
except ImportError:  # pragma: no cover - dépend de l'image
    HAS_TORCH = False
    torch = None  # type: ignore[assignment]


class InferenceError(RuntimeError):
    pass


def commentary_prompts(genes: List[str]) -> List[str]:
    return [f"Germline pathogenic variants in {g} are associated with" for g in genes]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Coupe à la dernière phrase complète : pas de fragment tronqué dans un rapport
    end = max(text.rfind("."), text.rfind(";"))
    return text[: end + 1] if end > 20 else ""


class BioGPTCommentator:
    def __init__(self, base_model: Optional[str] = None, adapter_path: Optional[str] = None, device: Optional[str] = None):
        cfg = biogpt()
        self.base_model = base_model or cfg.model
        self.adapter_path = adapter_path or cfg.adapter_path  # adaptateur LoRA BioGPT (optionnel)
        requested = (device or cfg.device).lower()
        if HAS_TORCH and requested in ("cuda", "auto"):
            if torch.cuda.is_available():
                self.device = "cuda"
            else:
                logger.warning("CUDA indisponible — BioGPT exécuté sur CPU (plus lent)")
                self.device = "cpu"
        else:
            self.device = "cpu"
        self.model = None
        self.tokenizer = None

    def load(self) -> None:
        if not HAS_TORCH:
            raise InferenceError("torch/transformers requis pour BioGPT")
        if self.model is not None:
            return
        torch.manual_seed(0)
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        model = AutoModelForCausalLM.from_pretrained(self.base_model, torch_dtype=dtype).to(self.device)
        if self.adapter_path and Path(self.adapter_path).is_dir():
            from peft import PeftModel

            logger.info(f"Adaptateur LoRA : {self.adapter_path}")
            model = PeftModel.from_pretrained(model, self.adapter_path).merge_and_unload()
        model.eval()
        self.model = model
        logger.info(f"BioGPT chargé ({self.base_model}, {self.device})")

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
        max_pos = getattr(self.model.config, "max_position_embeddings", 1024)
        inputs = self.tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=max_pos - max_new_tokens
        ).to(self.device)
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                num_beams=1,
                repetition_penalty=1.3,
                no_repeat_ngram_size=3,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = self.tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        return _clean(f"{prompt} {generated}")

    def comment_on_genes(self, genes: List[str]) -> str:
        sentences = [self.complete(p) for p in commentary_prompts(genes)]
        return " ".join(s for s in sentences if s)
