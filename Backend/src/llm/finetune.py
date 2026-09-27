"""BioGPT LoRA fine-tuning on the panel corpus, evaluation and controlled promotion.

Usage (conda env `genomic`, from Backend/):
    python -m src.llm.finetune corpus     # download/freeze PubMed and build train/val/test
    python -m src.llm.finetune train      # LoRA (early stopping on validation loss)
    python -m src.llm.finetune evaluate   # base vs adapter: perplexity + factual accuracy
    python -m src.llm.finetune all        # all three steps

Outputs under $LOCAL_DATA_ROOT/models/:
    biogpt_corpus/       frozen corpus + manifests (SHA-256)
    biogpt-germlineiq-lora/   adapter/ (best checkpoint), training_log.json, evaluation.json

Determinism: fixed seeds, deterministic CUDA algorithms, block order drawn from a fixed-seed
generator per epoch. BioGPT stays non-decisional: the adapter only changes the literature
commentary, itself verified (src.llm.knowledge) before entering the report.
"""

from __future__ import annotations

import os

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")  # required before CUDA initialisation

import argparse
import json
import math
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

from loguru import logger

from config.settings import paths
from src.llm.corpus import build_dataset, download_pubmed, load_texts

SEED = 42


@dataclass(frozen=True)
class TrainConfig:
    base_model: str = "microsoft/biogpt"
    block_size: int = 512
    batch_size: int = 8
    grad_accumulation: int = 2
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.06
    min_lr_ratio: float = 0.1
    max_epochs: int = 4
    eval_every_fraction: float = 0.5
    patience: int = 2
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple = ("q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2")
    max_grad_norm: float = 1.0
    seed: int = SEED


def corpus_dir() -> Path:
    return paths().models_dir / "biogpt_corpus"


def output_dir() -> Path:
    return paths().models_dir / "biogpt-germlineiq-lora"


def _seed_everything(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True, warn_only=True)


def _blocks(tokenizer, texts: List[str], block: int):
    import torch

    ids: List[int] = []
    for t in texts:
        ids += tokenizer(t, add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
    n = len(ids) // block
    return torch.tensor(ids[: n * block]).view(n, block)


def _lr_at(step: int, total: int, cfg: TrainConfig) -> float:
    warm = max(1, int(total * cfg.warmup_ratio))
    if step < warm:
        return cfg.learning_rate * (step + 1) / warm
    progress = (step - warm) / max(1, total - warm)
    cosine = 0.5 * (1 + math.cos(math.pi * progress))
    return cfg.learning_rate * (cfg.min_lr_ratio + (1 - cfg.min_lr_ratio) * cosine)


def _eval_loss(model, blocks, batch_size: int, device: str) -> float:
    import torch

    model.eval()
    total, n = 0.0, 0
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
        for i in range(0, len(blocks), batch_size):
            x = blocks[i:i + batch_size].to(device)
            loss = model(input_ids=x, labels=x).loss
            total += loss.item() * len(x)
            n += len(x)
    model.train()
    return total / n


def train(cfg: TrainConfig = TrainConfig()) -> Dict:
    import torch
    import transformers
    import peft
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    _seed_everything(cfg.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    cdir, out = corpus_dir(), output_dir()
    out.mkdir(parents=True, exist_ok=True)

    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
    train_blocks = _blocks(tokenizer, load_texts(cdir / "train.jsonl"), cfg.block_size)
    val_blocks = _blocks(tokenizer, load_texts(cdir / "val.jsonl"), cfg.block_size)
    logger.info(f"{cfg.block_size}-token blocks: train {len(train_blocks)}, val {len(val_blocks)}")

    model = AutoModelForCausalLM.from_pretrained(cfg.base_model, dtype=torch.float32).to(device)
    model = get_peft_model(model, LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=list(cfg.target_modules), bias="none", task_type="CAUSAL_LM",
    ))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    logger.info(f"Trainable parameters: {trainable:,} / {total_params:,} ({trainable / total_params:.2%})")

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=cfg.learning_rate, weight_decay=0.0)
    steps_per_epoch = math.ceil(len(train_blocks) / (cfg.batch_size * cfg.grad_accumulation))
    total_steps = steps_per_epoch * cfg.max_epochs
    eval_every = max(1, int(steps_per_epoch * cfg.eval_every_fraction))

    base_val = _eval_loss(model, val_blocks, cfg.batch_size, device)
    log = {"config": {**asdict(cfg), "target_modules": list(cfg.target_modules)}, "evals": [
        {"step": 0, "epoch": 0.0, "val_loss": round(base_val, 5), "val_perplexity": round(math.exp(base_val), 3)}
    ]}
    best, bad_evals, step, t0 = base_val, 0, 0, time.time()
    logger.info(f"Initial validation loss {base_val:.4f} (perplexity {math.exp(base_val):.2f})")

    model.train()
    stop = False
    for epoch in range(cfg.max_epochs):
        order = torch.randperm(len(train_blocks), generator=torch.Generator().manual_seed(cfg.seed + epoch))
        micro = [order[i:i + cfg.batch_size] for i in range(0, len(order), cfg.batch_size)]
        running = []
        for mi, idx in enumerate(micro):
            x = train_blocks[idx].to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
                loss = model(input_ids=x, labels=x).loss / cfg.grad_accumulation
            loss.backward()
            running.append(loss.item() * cfg.grad_accumulation)
            if (mi + 1) % cfg.grad_accumulation and mi + 1 != len(micro):
                continue
            for g in optimizer.param_groups:
                g["lr"] = _lr_at(step, total_steps, cfg)
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.max_grad_norm)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % eval_every == 0 or step == total_steps:
                val = _eval_loss(model, val_blocks, cfg.batch_size, device)
                train_loss = sum(running) / len(running)
                running = []
                entry = {
                    "step": step, "epoch": round(step / steps_per_epoch, 2),
                    "train_loss": round(train_loss, 5), "val_loss": round(val, 5),
                    "val_perplexity": round(math.exp(val), 3), "lr": _lr_at(step - 1, total_steps, cfg),
                }
                log["evals"].append(entry)
                logger.info(f"step {step}/{total_steps} epoch {entry['epoch']}: train {train_loss:.4f} val {val:.4f} (ppl {math.exp(val):.2f})")
                if val < best - 1e-4:
                    best, bad_evals = val, 0
                    model.save_pretrained(out / "adapter")
                    entry["saved"] = True
                else:
                    bad_evals += 1
                    if bad_evals >= cfg.patience:
                        logger.info("Early stopping: validation loss no longer improves")
                        stop = True
                        break
        if stop:
            break

    log.update({
        "best_val_loss": round(best, 5),
        "best_val_perplexity": round(math.exp(best), 3),
        "steps": step,
        "duration_s": round(time.time() - t0, 1),
        "trainable_parameters": trainable,
        "total_parameters": total_params,
        "train_blocks": len(train_blocks),
        "val_blocks": len(val_blocks),
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__},
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "corpus_manifest": json.loads((cdir / "dataset_manifest.json").read_text()),
    })
    (out / "training_log.json").write_text(json.dumps(log, indent=2, ensure_ascii=False))
    return log


def evaluate(cfg: TrainConfig = TrainConfig()) -> Dict:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from src.genomics import get_panel
    from src.genomics.clinvar import ClinVarIndex, default_clinvar_path
    from src.llm.inference_engine import GENERATION_CONFIG, generate_completion
    from src.llm.knowledge import GeneDiseaseKnowledge
    from src.llm.model_evaluator import acceptance, factual_evaluation, perplexity

    _seed_everything(cfg.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = output_dir()
    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
    test_texts = load_texts(corpus_dir() / "test.jsonl")
    clinvar = default_clinvar_path()
    knowledge = (
        GeneDiseaseKnowledge.from_clinvar_index(ClinVarIndex.load(clinvar, get_panel()))
        if clinvar.is_file() else GeneDiseaseKnowledge()
    )

    def run(model) -> Dict:
        model.eval()
        return {
            "perplexity": perplexity(model, tokenizer, test_texts, device, cfg.block_size),
            "factual": factual_evaluation(lambda p: generate_completion(model, tokenizer, p, device), knowledge),
        }

    base = AutoModelForCausalLM.from_pretrained(cfg.base_model, dtype=torch.float32).to(device)
    base_res = run(base)
    tuned = PeftModel.from_pretrained(base, out / "adapter").merge_and_unload()
    tuned_res = run(tuned)
    result = {
        "base": base_res,
        "fine_tuned": tuned_res,
        "acceptance": acceptance(base_res, tuned_res),
        "generation_config": GENERATION_CONFIG,
        "knowledge": knowledge.to_dict(),
        "test_documents": len(test_texts),
    }
    (out / "evaluation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="BioGPT LoRA fine-tuning (GermlineIQ)")
    parser.add_argument("step", choices=["corpus", "train", "evaluate", "all"])
    parser.add_argument("--lr", type=float, default=TrainConfig.learning_rate, help="learning rate")
    parser.add_argument("--epochs", type=int, default=TrainConfig.max_epochs, help="maximum epochs")
    parser.add_argument("--lora-r", type=int, default=TrainConfig.lora_r, help="LoRA rank")
    args = parser.parse_args()
    cfg = TrainConfig(learning_rate=args.lr, max_epochs=args.epochs, lora_r=args.lora_r, lora_alpha=2 * args.lora_r)
    if args.step in ("corpus", "all"):
        cdir = corpus_dir()
        print(json.dumps(build_dataset(download_pubmed(cdir), cdir)["splits"], indent=2))
    if args.step in ("train", "all"):
        log = train(cfg)
        print(f"Best validation perplexity: {log['best_val_perplexity']} ({log['steps']} steps, {log['duration_s']} s)")
    if args.step in ("evaluate", "all"):
        res = evaluate(cfg)
        for name in ("base", "fine_tuned"):
            r = res[name]
            print(name, "test perplexity", r["perplexity"]["perplexity"], json.dumps(r["factual"]["metrics"]))
        print("Promotion:", json.dumps(res["acceptance"], indent=2))


if __name__ == "__main__":
    main()
