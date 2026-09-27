"""BioGPT fine-tuning on the VCF statistics: dataset → LoRA training → evaluation → promotion.

Usage (conda env `genomic`, from Backend/):
    python -m src.llm.stats_finetune dataset    # facts + reference interpretations, frozen with SHA-256
    python -m src.llm.stats_finetune train      # LoRA, loss on the interpretation tokens only
    python -m src.llm.stats_finetune evaluate   # base vs fine-tuned, through the deterministic verifier
    python -m src.llm.stats_finetune promote    # copy the adapter to promoted/ ONLY if the gate passed
    python -m src.llm.stats_finetune all

Data (under $LOCAL_DATA_ROOT/models/biogpt-germlineiq-stats/):
- train / val / synthetic_test: facts sampled over realistic AND abnormal ranges (contamination,
  low depth, poor coverage, filtered calls…), statuses and risk levels computed by the same
  thresholds and rules as the pipeline; targets = reference interpretation with random paraphrases.
- real_test: facts of the real analyses of this server (1000 Genomes, GIAB NA12878, test VCFs),
  never used for training — the held-out evaluation of generalisation to real statistics.

Promotion gate (on synthetic_test AND real_test): sentence precision ≥ 0.95, topic coverage ≥ 0.90,
no wrong risk level, every final answer re-verified. The report never depends on the gate: without a
promoted adapter the final answer is the reference text.
"""

from __future__ import annotations

import os

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import argparse
import glob
import hashlib
import json
import math
import random
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from config.settings import paths
from src.llm.stats_interpretation import (
    FACTS_VERSION,
    REFERENCE_VERSION,
    VERIFIER_VERSION,
    assemble_final,
    extract_facts,
    facts_sha256,
    finalize_facts,
    reference_text,
    render_prompt,
)

SEED = 42
GATE = {"sentence_precision": 0.95, "topic_coverage": 0.90, "risk_errors": 0, "final_verified_rate": 1.0}


def root_dir() -> Path:
    return paths().models_dir / "biogpt-germlineiq-stats"


def dataset_dir() -> Path:
    return root_dir() / "dataset"


def candidate_dir() -> Path:
    return root_dir() / "candidate"


def promoted_dir() -> Path:
    return root_dir() / "promoted"


def _panel_genes():
    from src.genomics import get_panel

    panel = get_panel()
    germline = sorted(g.symbol for g in panel.genes.values() if g.is_germline_breast)
    high = sorted(g.symbol for g in panel.genes.values() if g.is_germline_breast and g.penetrance == "high")
    return sorted(panel.genes), germline, high


# --- Synthetic facts ---------------------------------------------------------------
def sample_facts(rng: random.Random, germline: Sequence[str], high: Sequence[str]) -> Dict[str, Any]:
    """Facts consistent with the pipeline thresholds and the rules germlineiq-rules-v1.1."""
    def mix(normal, abnormal, p_abnormal):
        lo, hi = (abnormal[rng.randrange(len(abnormal))] if rng.random() < p_abnormal else normal)
        return rng.uniform(lo, hi)

    fastq = rng.random() < 0.7
    small = rng.random() < 0.08
    variants = rng.randint(2, 29) if small else int(math.exp(rng.uniform(math.log(40), math.log(3000))))
    snv = round(variants * rng.uniform(0.70, 0.86)) if not small else rng.randint(0, variants)
    pass_rate = mix((0.82, 0.999), [(0.40, 0.79)], 0.15)
    moderate = sorted(set(germline) - set(high))

    scenario = rng.random()
    confirmed: List[str] = []
    to_confirm: List[str] = []
    if scenario < 0.20:
        confirmed = rng.sample(list(high), 2 if rng.random() < 0.1 else 1)
    elif scenario < 0.35:
        confirmed = rng.sample(moderate, 1)
    elif scenario < 0.50:
        to_confirm = rng.sample(list(germline), 2 if rng.random() < 0.15 else 1)
    elif scenario < 0.55:
        confirmed = rng.sample(list(germline), 1)
        to_confirm = rng.sample(sorted(set(germline) - set(confirmed)), 1)

    site_cov = weak = None
    if fastq:
        site_cov = mix((0.95, 1.0), [(0.90, 0.949), (0.40, 0.899)], 0.25)
        k = (rng.randint(1, 5) if site_cov < 0.95 else (rng.randint(1, 2) if rng.random() < 0.3 else 0))
        weak = rng.sample(list(germline), k)

    if any(g in high for g in confirmed):
        risk = "HIGH"
    elif confirmed:
        risk = "MODERATE"
    elif to_confirm or (fastq and site_cov < 0.90):
        risk = "INDETERMINATE"
    else:
        risk = "LOW"

    facts = {
        "mode": "FASTQ" if fastq else "VCF",
        "variants": variants,
        "snv": snv,
        "indel": variants - snv,
        "snv_pass": round(snv * pass_rate),
        "n_het": round(variants * rng.uniform(0.45, 0.75)),
        "pass_rate": pass_rate,
        "ti_tv": mix((1.85, 3.25), [(1.0, 1.79), (3.31, 4.2)], 0.2),
        "het_hom": mix((1.05, 2.95), [(0.3, 0.99), (3.05, 6.0)], 0.2),
        "het_vaf_median": mix((0.41, 0.59), [(0.25, 0.39), (0.61, 0.75)], 0.2),
        "median_depth": round(mix((16, 180), [(3, 14.5)], 0.15) * 2) / 2,
        "min_depth": 15,
        "mapped_rate": mix((0.955, 0.9999), [(0.60, 0.949)], 0.1) if fastq else None,
        "duplication_rate": mix((0.005, 0.29), [(0.31, 0.75)], 0.1) if fastq else None,
        "site_coverage": site_cov,
        "genes_not_excluded": weak or [],
        "confirmed": confirmed,
        "to_confirm": to_confirm,
        "low_vaf_calls": 0 if rng.random() < 0.6 else rng.randint(1, 25),
        "risk_level": risk,
    }
    return finalize_facts(facts)


def collect_real_facts() -> List[Dict[str, Any]]:
    """Facts of every analysis stored on this server (report + vcf_statistics.json)."""
    out, seen = [], set()
    for stats_file in sorted(glob.glob(str(paths().data_root / "patients" / "*" / "output" / "vcf_statistics.json"))):
        reports = sorted(glob.glob(stats_file.replace("vcf_statistics.json", "REP-*.json")))
        if not reports:
            continue
        report = json.loads(Path(reports[-1]).read_text())
        gf = report.get("genomic_findings", {})
        facts = extract_facts(
            json.loads(Path(stats_file).read_text()),
            report["clinical_prediction"]["risk_level"],
            [v["gene"] for v in gf.get("pathogenic_variants_detected", [])],
            [v["gene"] for v in gf.get("variants_to_confirm", [])],
        )
        h = facts_sha256(facts)
        if h in seen:
            continue
        seen.add(h)
        pid = Path(stats_file).parents[1].name
        out.append({"source": pid, "group": "real_genome" if pid[:2] in ("HG", "NA") else "test_vcf", "facts": facts})
    return out


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> str:
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    path.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def build_dataset(n_train: int = 4000, n_val: int = 300, n_test: int = 300, seed: int = SEED) -> Dict[str, Any]:
    out = dataset_dir()
    out.mkdir(parents=True, exist_ok=True)
    _, germline, high = _panel_genes()
    real = collect_real_facts()
    real_hashes = {facts_sha256(r["facts"]) for r in real}
    rng = random.Random(seed)

    def split(n: int, paraphrase: bool) -> List[Dict[str, Any]]:
        rows = []
        while len(rows) < n:
            facts = sample_facts(rng, germline, high)
            if facts_sha256(facts) in real_hashes:
                continue
            rows.append({"facts": facts, "prompt": render_prompt(facts),
                         "target": reference_text(facts, rng if paraphrase else None)})
        return rows

    splits = {"train": split(n_train, True), "val": split(n_val, True), "synthetic_test": split(n_test, False)}
    splits["real_test"] = [{**r, "prompt": render_prompt(r["facts"]), "target": reference_text(r["facts"])} for r in real]
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": seed,
        "versions": {"facts": FACTS_VERSION, "reference": REFERENCE_VERSION, "verifier": VERIFIER_VERSION},
        "splits": {},
    }
    for name, rows in splits.items():
        manifest["splits"][name] = {
            "examples": len(rows),
            "sha256": _write_jsonl(out / f"{name}.jsonl", rows),
            "risk_levels": {r: sum(1 for x in rows if x["facts"]["risk_level"] == r) for r in ("HIGH", "MODERATE", "INDETERMINATE", "LOW")},
            "fastq_mode": sum(1 for x in rows if x["facts"]["mode"] == "FASTQ"),
        }
    manifest["splits"]["real_test"]["sources"] = [r["source"] for r in real]
    (out / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def load_split(name: str) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in (dataset_dir() / f"{name}.jsonl").read_text().splitlines() if line.strip()]


# --- Training ----------------------------------------------------------------------
@dataclass(frozen=True)
class StatsTrainConfig:
    base_model: str = "microsoft/biogpt"
    batch_size: int = 8
    grad_accumulation: int = 2
    learning_rate: float = 3e-4
    warmup_ratio: float = 0.05
    max_epochs: int = 3
    evals_per_epoch: int = 2
    patience: int = 2
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: tuple = ("q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2")
    max_grad_norm: float = 1.0
    max_length: int = 768
    seed: int = SEED


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


def _encode(tokenizer, rows: List[Dict[str, Any]], max_length: int):
    """input_ids = prompt + target + eos; labels = -100 on the prompt (loss on the interpretation only)."""
    encoded = []
    for r in rows:
        p = tokenizer(r["prompt"])["input_ids"]
        t = tokenizer(r["target"], add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
        if len(p) + len(t) > max_length:
            raise ValueError(f"example of {len(p) + len(t)} tokens > max_length {max_length}")
        encoded.append((p + t, [-100] * len(p) + t))
    return encoded


def _collate(batch, pad_id: int):
    import torch

    n = max(len(ids) for ids, _ in batch)
    ids = torch.full((len(batch), n), pad_id, dtype=torch.long)
    labels = torch.full((len(batch), n), -100, dtype=torch.long)
    mask = torch.zeros((len(batch), n), dtype=torch.long)
    for i, (x, y) in enumerate(batch):
        ids[i, :len(x)] = torch.tensor(x)
        labels[i, :len(y)] = torch.tensor(y)
        mask[i, :len(x)] = 1
    return ids, labels, mask


def _eval_loss(model, data, cfg: StatsTrainConfig, pad_id: int, device: str) -> float:
    import torch

    model.eval()
    total, tokens = 0.0, 0
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
        for i in range(0, len(data), cfg.batch_size):
            ids, labels, mask = (t.to(device) for t in _collate(data[i:i + cfg.batch_size], pad_id))
            n = int((labels[:, 1:] != -100).sum())
            total += model(input_ids=ids, attention_mask=mask, labels=labels).loss.item() * n
            tokens += n
    model.train()
    return total / tokens


def train(cfg: StatsTrainConfig = StatsTrainConfig()) -> Dict[str, Any]:
    import peft
    import torch
    import transformers
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    _seed_everything(cfg.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = candidate_dir()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
    pad_id = tokenizer.pad_token_id
    train_data = _encode(tokenizer, load_split("train"), cfg.max_length)
    val_data = _encode(tokenizer, load_split("val"), cfg.max_length)
    lengths = [len(x) for x, _ in train_data]
    logger.info(f"Examples: train {len(train_data)}, val {len(val_data)}; tokens per example max {max(lengths)}, mean {sum(lengths) / len(lengths):.0f}")

    model = AutoModelForCausalLM.from_pretrained(cfg.base_model, dtype=torch.float32).to(device)
    model = get_peft_model(model, LoraConfig(
        r=cfg.lora_r, lora_alpha=cfg.lora_alpha, lora_dropout=cfg.lora_dropout,
        target_modules=list(cfg.target_modules), bias="none", task_type="CAUSAL_LM",
    ))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=cfg.learning_rate, weight_decay=0.0)
    steps_per_epoch = math.ceil(len(train_data) / (cfg.batch_size * cfg.grad_accumulation))
    total_steps = steps_per_epoch * cfg.max_epochs
    warm = max(1, int(total_steps * cfg.warmup_ratio))
    eval_every = max(1, steps_per_epoch // cfg.evals_per_epoch)

    def lr_at(step: int) -> float:
        if step < warm:
            return cfg.learning_rate * (step + 1) / warm
        return cfg.learning_rate * 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total_steps - warm)))

    base_val = _eval_loss(model, val_data, cfg, pad_id, device)
    log: Dict[str, Any] = {"config": {**asdict(cfg), "target_modules": list(cfg.target_modules)},
                           "evals": [{"step": 0, "epoch": 0.0, "val_loss": round(base_val, 5)}]}
    logger.info(f"Initial validation loss (interpretation tokens) {base_val:.4f}")
    best, bad, step, t0, stop = base_val, 0, 0, time.time(), False
    model.train()
    for epoch in range(cfg.max_epochs):
        order = torch.randperm(len(train_data), generator=torch.Generator().manual_seed(cfg.seed + epoch)).tolist()
        micro = [order[i:i + cfg.batch_size] for i in range(0, len(order), cfg.batch_size)]
        running: List[float] = []
        for mi, idx in enumerate(micro):
            ids, labels, mask = (t.to(device) for t in _collate([train_data[j] for j in idx], pad_id))
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=device == "cuda"):
                loss = model(input_ids=ids, attention_mask=mask, labels=labels).loss / cfg.grad_accumulation
            loss.backward()
            running.append(loss.item() * cfg.grad_accumulation)
            if (mi + 1) % cfg.grad_accumulation and mi + 1 != len(micro):
                continue
            for g in optimizer.param_groups:
                g["lr"] = lr_at(step)
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.max_grad_norm)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            step += 1
            if step % eval_every == 0 or step == total_steps:
                val = _eval_loss(model, val_data, cfg, pad_id, device)
                entry = {"step": step, "epoch": round(step / steps_per_epoch, 2),
                         "train_loss": round(sum(running) / len(running), 5), "val_loss": round(val, 5)}
                running = []
                log["evals"].append(entry)
                logger.info(f"step {step}/{total_steps} epoch {entry['epoch']}: train {entry['train_loss']:.4f} val {val:.4f}")
                if val < best - 1e-4:
                    best, bad = val, 0
                    model.save_pretrained(out / "adapter")
                    entry["saved"] = True
                else:
                    bad += 1
                    if bad >= cfg.patience:
                        logger.info("Early stopping")
                        stop = True
                        break
        if stop:
            break

    log.update({
        "best_val_loss": round(best, 5),
        "steps": step,
        "duration_s": round(time.time() - t0, 1),
        "trainable_parameters": trainable,
        "total_parameters": total_params,
        "versions": {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__},
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "dataset_manifest": json.loads((dataset_dir() / "dataset_manifest.json").read_text()),
    })
    (out / "training_log.json").write_text(json.dumps(log, indent=2))
    return log


# --- Evaluation --------------------------------------------------------------------
def score(rows: List[Dict[str, Any]], outputs: List[str], genes: Sequence[str]) -> Dict[str, Any]:
    """Metrics of raw model outputs through the deterministic verifier."""
    gen = ver = req = from_model = final_ok = risk_err = 0
    num_claims = num_ok = status_claims = status_ok = 0
    examples = []
    for r, text in zip(rows, outputs):
        res = assemble_final(text, r["facts"], genes)
        m = res["metrics"]
        gen += m["generated_sentences"]
        ver += m["verified_sentences"]
        req += m["required_topics"]
        from_model += m["topics_from_model"]
        final_ok += int(res["final_verified"])
        claims = [c for s in res["sentences"] for c in s["claims"]]
        risk_err += int(any(c["type"] == "risk" and not c["ok"] for c in claims))
        num = [c for c in claims if c["type"] == "number"]
        st = [c for c in claims if c["type"] == "status"]
        num_claims, num_ok = num_claims + len(num), num_ok + sum(c["ok"] for c in num)
        status_claims, status_ok = status_claims + len(st), status_ok + sum(c["ok"] for c in st)
        if len(examples) < 5 or r.get("source"):
            examples.append({"source": r.get("source"), "output": text[:1500],
                             "rejected": [s for s in res["sentences"] if not s["verified"]][:5],
                             "topics_from_model": m["topics_from_model"], "required_topics": m["required_topics"]})
    n = len(rows)
    return {
        "examples_scored": n,
        "sentence_precision": round(ver / gen, 4) if gen else 0.0,
        "topic_coverage": round(from_model / req, 4) if req else 0.0,
        "number_accuracy": round(num_ok / num_claims, 4) if num_claims else None,
        "status_accuracy": round(status_ok / status_claims, 4) if status_claims else None,
        "risk_errors": risk_err,
        "final_verified_rate": round(final_ok / n, 4) if n else 0.0,
        "generated_sentences": gen,
        "verified_sentences": ver,
        "samples": examples,
    }


def _gate(metrics: Dict[str, Any]) -> Dict[str, Any]:
    checks = {
        "sentence_precision": metrics["sentence_precision"] >= GATE["sentence_precision"],
        "topic_coverage": metrics["topic_coverage"] >= GATE["topic_coverage"],
        "risk_errors": metrics["risk_errors"] <= GATE["risk_errors"],
        "final_verified_rate": metrics["final_verified_rate"] >= GATE["final_verified_rate"],
    }
    return {"passed": all(checks.values()), "checks": checks}


def evaluate(cfg: StatsTrainConfig = StatsTrainConfig(), base_examples: int = 40) -> Dict[str, Any]:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from src.llm.stats_model import generate_batch

    _seed_everything(cfg.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    genes, _, _ = _panel_genes()
    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
    synth, real = load_split("synthetic_test"), load_split("real_test")

    def run(model, rows):
        t0 = time.time()
        outputs = generate_batch(model, tokenizer, [r["prompt"] for r in rows], device)
        res = score(rows, outputs, genes)
        res["generation_s"] = round(time.time() - t0, 1)
        return res

    dtype = torch.float16 if device == "cuda" else torch.float32
    base = AutoModelForCausalLM.from_pretrained(cfg.base_model, dtype=dtype).to(device).eval()
    base_res = {"synthetic_test": run(base, synth[:base_examples]), "real_test": run(base, real)}
    tuned = PeftModel.from_pretrained(base, candidate_dir() / "adapter").merge_and_unload().eval()
    tuned_res = {"synthetic_test": run(tuned, synth), "real_test": run(tuned, real)}
    gate = {name: _gate(tuned_res[name]) for name in ("synthetic_test", "real_test")}
    result = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_thresholds": GATE,
        "base_zero_shot": base_res,
        "fine_tuned": tuned_res,
        "promotion": {"passed": all(g["passed"] for g in gate.values()), "per_split": gate},
        "dataset_manifest": json.loads((dataset_dir() / "dataset_manifest.json").read_text()),
    }
    (candidate_dir() / "evaluation.json").write_text(json.dumps(result, indent=2))
    return result


def promote() -> Dict[str, Any]:
    """Copies the candidate adapter to promoted/ only if its evaluation passed the gate."""
    cand = candidate_dir()
    ev_path = cand / "evaluation.json"
    if not ev_path.is_file():
        raise SystemExit("No evaluation: run `python -m src.llm.stats_finetune evaluate` first")
    ev = json.loads(ev_path.read_text())
    if not ev["promotion"]["passed"]:
        raise SystemExit(f"Promotion refused: gate not passed {json.dumps(ev['promotion']['per_split'])}")
    history = root_dir() / "history"
    history.mkdir(parents=True, exist_ok=True)
    version = f"v{len([p for p in history.iterdir() if p.is_dir()]) + 1}"
    shutil.copytree(cand, history / version)
    if promoted_dir().exists():
        shutil.rmtree(promoted_dir())
    shutil.copytree(cand, promoted_dir())
    manifest = {
        "version": version,
        "promoted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fine_tuned": {k: {m: v[m] for m in ("sentence_precision", "topic_coverage", "number_accuracy", "status_accuracy", "risk_errors", "final_verified_rate", "examples_scored")}
                       for k, v in ev["fine_tuned"].items()},
        "dataset_sha256": {k: v["sha256"] for k, v in ev["dataset_manifest"]["splits"].items()},
    }
    (promoted_dir() / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="BioGPT fine-tuning on VCF statistics (GermlineIQ)")
    parser.add_argument("step", choices=["dataset", "train", "evaluate", "promote", "all"])
    parser.add_argument("--epochs", type=int, default=StatsTrainConfig.max_epochs)
    parser.add_argument("--lr", type=float, default=StatsTrainConfig.learning_rate)
    parser.add_argument("--train-examples", type=int, default=4000)
    args = parser.parse_args()
    cfg = StatsTrainConfig(max_epochs=args.epochs, learning_rate=args.lr)
    if args.step in ("dataset", "all"):
        print(json.dumps(build_dataset(n_train=args.train_examples)["splits"], indent=2))
    if args.step in ("train", "all"):
        log = train(cfg)
        print(f"Best validation loss {log['best_val_loss']} ({log['steps']} steps, {log['duration_s']} s)")
    if args.step in ("evaluate", "all"):
        res = evaluate(cfg)
        for model in ("base_zero_shot", "fine_tuned"):
            for split, m in res[model].items():
                print(f"{model:15s} {split:15s} precision {m['sentence_precision']:.3f} coverage {m['topic_coverage']:.3f} "
                      f"numbers {m['number_accuracy']} status {m['status_accuracy']} risk_errors {m['risk_errors']} n={m['examples_scored']}")
        print("Promotion gate:", json.dumps(res["promotion"], indent=2))
    if args.step in ("promote", "all"):
        print(json.dumps(promote(), indent=2))


if __name__ == "__main__":
    main()
