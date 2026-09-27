"""BioGPT statistics interpreter: model loading and generation (step 3 of the workflow).

The fine-tuned LoRA adapter (src.llm.stats_finetune) turns the facts of a vcf_statistics.json into
an interpretation paragraph; src.llm.stats_interpretation then verifies every sentence and builds
the final answer. Greedy decoding: same adapter, same facts → same text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from config.settings import biogpt, biogpt_stats
from src.llm.stats_interpretation import assemble_final, render_prompt

MAX_NEW_TOKENS = 400
GENERATION = {"do_sample": False, "num_beams": 1, "repetition_penalty": 1.0}


def generate_batch(model, tokenizer, prompts: Sequence[str], device: str, batch_size: int = 16,
                   max_new_tokens: int = MAX_NEW_TOKENS) -> List[str]:
    """Greedy generation, left padding (BioGPT positions follow the attention mask)."""
    import torch

    tokenizer.padding_side = "left"
    outputs: List[str] = []
    for i in range(0, len(prompts), batch_size):
        batch = list(prompts[i:i + batch_size])
        enc = tokenizer(batch, return_tensors="pt", padding=True).to(device)
        with torch.no_grad():
            out = model.generate(
                **enc, **GENERATION, max_new_tokens=max_new_tokens,
                pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id,
            )
        for row in out[:, enc["input_ids"].shape[1]:]:
            outputs.append(tokenizer.decode(row, skip_special_tokens=True).strip())
    return outputs


class StatsInterpreter:
    """Base BioGPT + promoted statistics adapter. Load, interpret, unload (VRAM is shared)."""

    def __init__(self, adapter_path: Optional[str] = None, device: Optional[str] = None):
        cfg = biogpt_stats()
        self.base_model = biogpt().model
        self.adapter_path = adapter_path or cfg.adapter_path
        self.requested_device = (device or biogpt().device).lower()
        self.model = None
        self.tokenizer = None
        self.device = "cpu"

    @property
    def available(self) -> bool:
        return bool(self.adapter_path) and (Path(self.adapter_path) / "adapter_config.json").is_file()

    def load(self) -> None:
        if self.model is not None:
            return
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = "cuda" if self.requested_device in ("auto", "cuda") and torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_model)
        model = AutoModelForCausalLM.from_pretrained(self.base_model, dtype=dtype).to(self.device)
        model = PeftModel.from_pretrained(model, self.adapter_path).merge_and_unload()
        model.eval()
        self.model = model
        logger.info(f"BioGPT statistics interpreter loaded ({self.adapter_path}, {self.device})")

    def unload(self) -> None:
        from src.utils.gpu_manager import get_gpu_manager

        gpu = get_gpu_manager()
        if self.model is not None:
            gpu.unload_hf_model(self.model)
        self.model = None
        self.tokenizer = None
        gpu.empty_cuda_cache()

    def generate(self, facts: Dict[str, Any]) -> str:
        self.load()
        return generate_batch(self.model, self.tokenizer, [render_prompt(facts)], self.device, batch_size=1)[0]

    def interpret(self, facts: Dict[str, Any], gene_symbols: Sequence[str]) -> Dict[str, Any]:
        raw = self.generate(facts)
        result = assemble_final(raw, facts, gene_symbols)
        result.update({"model_output": raw, "model": self.base_model, "adapter": self.adapter_path})
        return result
