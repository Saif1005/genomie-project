"""GermlineIQ multi-agent system benchmark.

Six suites, each producing verifiable numbers (JSON + Markdown report):

  clinical        Real 1000 Genomes cohort (30x reads over the panel genes), full FASTQ → report
                  pipeline through the HTTP API. Detected P/LP variants and risk levels are
                  compared with the independent ground truth (scripts/find_1000g_carriers.py).
  analytical      GIAB HG001 (NA12878) truth set: precision / recall of the variant calls on the
                  panel regions covered by the exome (bcftools isec on normalised VCFs).
  reproducibility The same inputs submitted several times → identical clinical content and
                  statistics (SHA-256 of the canonical report, timestamps excluded).
  orchestration   Deterministic router vs Mistral router (in-process engine): identical plan and
                  report, orchestration overhead (total time − agent time), router latency.
  robustness      Fault injection through the API (invalid id, path outside the data root,
                  missing file, identical R1/R2, malformed VCF…) → clean rejection, never a crash.
  latency         API latency (p50 / p95 / p99) of the read endpoints.

Usage (conda env "genomic", backend running, from Backend/):
    python -m benchmarks.multiagent --suites all
    python -m benchmarks.multiagent --suites clinical,robustness --out benchmarks/results
The latest result is also written to $LOCAL_DATA_ROOT/benchmarks/latest.json (served by the API).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

BACKEND = os.getenv("BENCH_BASE_URL", "http://127.0.0.1:8000")
DATA = Path(os.getenv("LOCAL_DATA_ROOT", str(Path.home() / "germlineiq_data")))
ROOT = Path(__file__).resolve().parents[2]
PANEL_JSON = ROOT / "Backend/data/cancer_genes/cancer_genes_db.json"
BENCHMARK_VERSION = "germlineiq-bench-v1"


# --- Helpers ------------------------------------------------------------------------
def sh(cmd: str) -> str:
    p = subprocess.run(["bash", "-c", f"set -o pipefail; {cmd}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd}\n{p.stderr[-600:]}")
    return p.stdout


def pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    h = (len(xs) - 1) * q
    lo = int(h)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (h - lo) * (xs[hi] - xs[lo]), 3)


def summary(values: List[float]) -> Dict[str, Any]:
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 3) if values else None,
        "median": pct(values, 0.5),
        "p95": pct(values, 0.95),
        "min": round(min(values), 3) if values else None,
        "max": round(max(values), 3) if values else None,
    }


def wilson(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    """95 % Wilson score interval for a proportion k/n."""
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def canonical_hash(report: Dict[str, Any]) -> str:
    """SHA-256 of the clinical content only: findings, conclusion, statistics and reproducibility.

    Technical metadata (timestamps, report id, file paths, execution time, hardware, router name)
    is excluded: it legitimately differs between runs and routers without any clinical meaning.
    """
    r = json.loads(json.dumps(report))
    clinical = {k: r.get(k) for k in ("genomic_findings", "clinical_prediction", "statistics", "reproducibility")}
    stats = clinical.get("statistics") or {}
    if stats.get("alignment"):
        stats["alignment"].pop("bam", None)
    return hashlib.sha256(json.dumps(clinical, sort_keys=True).encode()).hexdigest()


class Api:
    def __init__(self, base: str = BACKEND):
        self.base = base
        self.client = httpx.Client(base_url=base, timeout=60)

    def submit_fastq(self, patient: str, r1: str, r2: str) -> str:
        r = self.client.post("/api/v1/analyze", json={"patient_id": patient, "fastq_r1": r1, "fastq_r2": r2})
        r.raise_for_status()
        return r.json()["job_id"]

    def submit_vcf(self, patient: str, vcf: str) -> str:
        r = self.client.post("/api/v1/analyze/vcf", json={"patient_id": patient, "vcf_path": vcf})
        r.raise_for_status()
        return r.json()["job_id"]

    def wait(self, job_id: str, timeout: float = 7200, poll: float = 3.0) -> Dict[str, Any]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            job = self.client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(poll)
        raise TimeoutError(job_id)


def panel_penetrance() -> Dict[str, str]:
    genes = json.loads(PANEL_JSON.read_text())
    return {s: (g.get("breast_panel") or {}).get("penetrance", "") for s, g in genes.items()
            if (g.get("breast_panel") or {}).get("role") == "germline"}


# --- Suite: clinical accuracy on the real cohort ---------------------------------------------
def expected_risk(expected: List[Dict], penetrance: Dict[str, str]) -> str:
    if not expected:
        return "LOW"
    return "HIGH" if any(penetrance.get(e["gene"]) == "high" for e in expected) else "MODERATE"


def reported_variants(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    gf = report.get("genomic_findings") or {}
    return [dict(v, _class=cls) for cls in ("pathogenic_variants_detected", "variants_to_confirm") for v in gf.get(cls) or []]


def suite_clinical(api: Api, log: Callable) -> Dict[str, Any]:
    manifest = json.loads((DATA / "reference/1000g/cohort_manifest.json").read_text())
    penetrance = panel_penetrance()
    samples = []
    for s in manifest["samples"]:
        log(f"clinical: {s['sample']} ({s['description']})")
        t0 = time.time()
        job = api.wait(api.submit_fastq(s["sample"], s["fastq_r1"], s["fastq_r2"]))
        wall = time.time() - t0
        report = job.get("result") or {}
        exp_keys = {e["variant"] for e in s["expected_plp"]}
        found = reported_variants(report)
        stats = report.get("statistics") or {}
        found_keys, found_detail = set(), {}
        for v in (stats.get("variants") or []):
            if v["category"] in ("pathogenic_confirmed", "pathogenic_to_confirm", "lof_to_confirm"):
                key = f"{v['chromosome']}:{v['position']}:{v['ref']}:{v['alt']}"
                found_keys.add(key)
                found_detail[key] = {"gene": v["gene"], "category": v["category"], "vaf": v["vaf"], "dp": v["dp"],
                                     "qc_flags": v["qc_flags"], "clinvar": v["clinvar_significance"]}
        exp_risk = expected_risk(s["expected_plp"], penetrance)
        got_risk = (report.get("clinical_prediction") or {}).get("risk_level")
        aln = stats.get("alignment") or {}
        cov = aln.get("clinvar_sites_coverage") or {}
        samples.append({
            "sample": s["sample"],
            "description": s["description"],
            "read_pairs": s["read_pairs"],
            "status": job["status"],
            "error": job.get("error"),
            "wall_time_s": round(wall, 2),
            "engine_time_s": job.get("duration_s"),
            "step_timings": job.get("step_timings", []),
            "expected_variants": sorted(exp_keys),
            "detected_variants": sorted(found_keys),
            "true_positive_variants": sorted(exp_keys & found_keys),
            "missed_variants": sorted(exp_keys - found_keys),
            "unexpected_variants": sorted(found_keys - exp_keys),
            "unexpected_detail": {k: found_detail[k] for k in sorted(found_keys - exp_keys)},
            "reported_findings": [{"gene": v["gene"], "mutation": v["mutation"], "class": v["_class"],
                                   "pathogenicity": v.get("pathogenicity"), "zygosity": v.get("zygosity"),
                                   "dp": (v.get("gatk_metrics") or {}).get("DP"),
                                   "vaf": (v.get("gatk_metrics") or {}).get("VAF")} for v in found],
            "expected_risk": exp_risk,
            "reported_risk": got_risk,
            "risk_correct": got_risk == exp_risk,
            "conclusion": (report.get("clinical_prediction") or {}).get("diagnostic_conclusion"),
            "mapped_rate": aln.get("mapped_rate"),
            "duplication_rate": aln.get("duplication_rate"),
            "pathogenic_sites_coverage": cov.get("fraction_covered"),
            "median_site_depth": cov.get("median_depth"),
            "variants_in_panel": (stats.get("panel") or {}).get("variants_in_panel"),
            "ti_tv": (stats.get("panel") or {}).get("ti_tv"),
            "quality_warnings": (report.get("clinical_prediction") or {}).get("quality_warnings", []),
        })

    carriers = [x for x in samples if x["expected_variants"]]
    controls = [x for x in samples if not x["expected_variants"]]
    tp_samples = sum(1 for x in carriers if not x["missed_variants"])
    tn_samples = sum(1 for x in controls if not x["detected_variants"])
    exp_var = sum(len(x["expected_variants"]) for x in samples)
    tp_var = sum(len(x["true_positive_variants"]) for x in samples)
    fp_confirmed = sum(1 for x in samples for d in x["unexpected_detail"].values() if d["category"] == "pathogenic_confirmed")
    flagged = sum(1 for x in samples for d in x["unexpected_detail"].values() if d["category"] != "pathogenic_confirmed")
    risk_ok = sum(1 for x in samples if x["risk_correct"])
    per_agent: Dict[str, List[float]] = {}
    for x in samples:
        for st in x["step_timings"]:
            if st.get("status") == "completed":
                per_agent.setdefault(st["tool"], []).append(st["duration"])
    return {
        "cohort": manifest["source"],
        "samples": samples,
        "metrics": {
            "samples": len(samples),
            "completed": sum(1 for x in samples if x["status"] == "completed"),
            "carrier_sensitivity": round(tp_samples / len(carriers), 4) if carriers else None,
            "carrier_sensitivity_ci95": wilson(tp_samples, len(carriers)),
            "control_specificity": round(tn_samples / len(controls), 4) if controls else None,
            "control_specificity_ci95": wilson(tn_samples, len(controls)),
            "variant_recall": round(tp_var / exp_var, 4) if exp_var else None,
            "unexpected_confirmed_plp": fp_confirmed,
            "unexpected_flagged_to_confirm": flagged,
            "risk_level_accuracy": round(risk_ok / len(samples), 4) if samples else None,
            "risk_level_accuracy_ci95": wilson(risk_ok, len(samples)),
            "end_to_end_s": summary([x["wall_time_s"] for x in samples]),
            "per_agent_s": {k: summary(v) for k, v in sorted(per_agent.items())},
        },
    }


# --- Suite: analytical validity (GIAB) -----------------------------------------------------
def suite_analytical(api: Api, log: Callable) -> Dict[str, Any]:
    out = DATA / "patients/NA12878/output"
    truth_vcf = DATA / "reference/giab/HG001_GRCh38_1_22_v4.2.1_benchmark.vcf.gz"
    truth_bed = DATA / "reference/giab/HG001_GRCh38_1_22_v4.2.1_benchmark.bed"
    ref = DATA / "reference/hg38/hg38.fa"
    if not ((out / "variants.vcf.gz").is_file() and (out / "aligned.bam").is_file() and truth_vcf.is_file()):
        return {"skipped": "NA12878 FASTQ run or GIAB truth set missing"}
    log("analytical: GIAB HG001 concordance on the covered panel regions")
    w = Path(tempfile.mkdtemp(prefix="giab_"))
    genes = json.loads(PANEL_JSON.read_text())
    (w / "panel.bed").write_text("".join(
        f"{g['chromosome']}\t{g['start_position'] - 101}\t{g['end_position'] + 100}\n"
        for g in genes.values() if (g.get("breast_panel") or {}).get("role") == "germline"))
    sh(f"sort -k1,1 -k2,2n {w}/panel.bed > {w}/panel.sorted.bed")
    sh(f"samtools depth -b {w}/panel.sorted.bed -Q 20 -q 20 {out}/aligned.bam | awk '$3>=15{{print $1\"\\t\"$2-1\"\\t\"$2}}' | bedtools merge > {w}/covered.bed")
    sh(f"bedtools intersect -a {w}/panel.sorted.bed -b {truth_bed} | sort -k1,1 -k2,2n | bedtools merge | bedtools intersect -a - -b {w}/covered.bed | sort -k1,1 -k2,2n | bedtools merge > {w}/eval.bed")
    size = int(sh(f"awk '{{s+=$3-$2}}END{{print s+0}}' {w}/eval.bed").strip())

    def norm(src: Path, dst: Path, filt: str) -> None:
        sh(f"bcftools view -R {w}/eval.bed {filt} {src} -Ou | bcftools norm -m -any -f {ref} -Ou 2>/dev/null | "
           f"bcftools view -e 'ALT=\"*\"' -Oz -o {dst} && bcftools index -f -t {dst}")

    norm(truth_vcf, w / "truth.vcf.gz", "")
    results = {}
    for label, src, filt in (("pass_only", out / "variants.vcf.gz", "-f PASS,."), ("all_calls", out / "variants.raw.vcf.gz", "")):
        norm(src, w / f"{label}.vcf.gz", filt)
        sh(f"rm -rf {w}/isec_{label} && bcftools isec -p {w}/isec_{label} -Oz {w}/truth.vcf.gz {w}/{label}.vcf.gz")
        count = lambda f: int(sh(f"bcftools view -H {w}/isec_{label}/{f} | wc -l").strip())  # noqa: E731
        fn, fp, tp = count("0000.vcf.gz"), count("0001.vcf.gz"), count("0002.vcf.gz")
        prec, rec = (tp / (tp + fp) if tp + fp else None), (tp / (tp + fn) if tp + fn else None)
        results[label] = {
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": round(prec, 4) if prec is not None else None,
            "recall": round(rec, 4) if rec is not None else None,
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec and rec else None,
            "recall_ci95": wilson(tp, tp + fn),
        }
    return {"sample": "NA12878 / GIAB HG001 v4.2.1 (exome SRR1611178)", "evaluated_bp": size,
            "region": "panel genes ± 100 bp ∩ GIAB high-confidence ∩ depth ≥ 15x", **results}


# --- Suite: reproducibility ---------------------------------------------------------------
def suite_reproducibility(api: Api, log: Callable, runs: int = 3) -> Dict[str, Any]:
    cases = []
    smoke = ROOT / "scripts/testdata/smoke_brca.vcf"
    inp = DATA / "patients/BENCH_REPRO/input"
    inp.mkdir(parents=True, exist_ok=True)
    (inp / "smoke_brca.vcf").write_bytes(smoke.read_bytes())
    cases.append(("VCF — synthetic BRCA1/BRCA2", "vcf", str(inp / "smoke_brca.vcf"), None))
    manifest = json.loads((DATA / "reference/1000g/cohort_manifest.json").read_text())
    s = manifest["samples"][0]
    cases.append((f"FASTQ — {s['sample']} (cached variant calling)", "fastq", s["fastq_r1"], s["fastq_r2"]))
    out = []
    for label, mode, a, b in cases:
        log(f"reproducibility: {label} × {runs}")
        hashes, stat_hashes, times = [], [], []
        for i in range(runs):
            pid = "BENCH_REPRO" if mode == "vcf" else s["sample"]
            t0 = time.time()
            job = api.wait(api.submit_vcf(pid, a) if mode == "vcf" else api.submit_fastq(pid, a, b))
            times.append(time.time() - t0)
            rep = job.get("result") or {}
            hashes.append(canonical_hash(rep))
            stat_hashes.append(hashlib.sha256(json.dumps(rep.get("statistics"), sort_keys=True).encode()).hexdigest())
        out.append({"case": label, "runs": runs, "identical_reports": len(set(hashes)) == 1,
                    "identical_statistics": len(set(stat_hashes)) == 1, "report_sha256": hashes[0],
                    "time_s": summary(times)})
    return {"cases": out, "all_identical": all(c["identical_reports"] and c["identical_statistics"] for c in out)}


# --- Suite: orchestration (in-process engine) ------------------------------------------------
def suite_orchestration(api: Api, log: Callable, runs: int = 3) -> Dict[str, Any]:
    sys.path.insert(0, str(ROOT / "Backend"))
    os.environ.setdefault("PYTHONPATH", str(ROOT / "Backend"))
    from src.orchestration.engine import Orchestrator  # noqa: E402

    inp = DATA / "patients/BENCH_ORCH/input"
    inp.mkdir(parents=True, exist_ok=True)
    vcf = inp / "smoke_brca.vcf"
    vcf.write_bytes((ROOT / "scripts/testdata/smoke_brca.vcf").read_bytes())
    ctx = {"patient_id": "BENCH_ORCH", "vcf_uri": str(vcf)}
    res = {}
    previous = os.environ.get("ORCHESTRATOR_DETERMINISTIC")
    for name, flag in (("deterministic", "true"), ("mistral", "false")):
        os.environ["ORCHESTRATOR_DETERMINISTIC"] = flag
        log(f"orchestration: {name} router × {runs}")
        totals, agents, overheads, plans, hashes = [], [], [], [], []
        for _ in range(runs):
            t0 = time.perf_counter()
            r = Orchestrator().run(dict(ctx))
            total = time.perf_counter() - t0
            agent_time = sum(s.duration for s in r.steps)
            totals.append(total)
            agents.append(agent_time)
            overheads.append(total - agent_time)
            plans.append(tuple(r.plan))
            hashes.append(canonical_hash(r.context.get("clinical_report") or {}))
        res[name] = {"router": r.router, "success": r.success, "plan": list(plans[0]),
                     "total_s": summary(totals), "agent_s": summary(agents), "overhead_s": summary(overheads),
                     "report_sha256": hashes[0], "stable_across_runs": len(set(hashes)) == 1}
    if previous is None:
        os.environ.pop("ORCHESTRATOR_DETERMINISTIC", None)
    else:
        os.environ["ORCHESTRATOR_DETERMINISTIC"] = previous
    res["same_plan"] = res["deterministic"]["plan"] == res["mistral"]["plan"]
    res["same_clinical_report"] = res["deterministic"]["report_sha256"] == res["mistral"]["report_sha256"]
    return res


# --- Suite: robustness (fault injection) ---------------------------------------------------
def suite_robustness(api: Api, log: Callable) -> Dict[str, Any]:
    root = DATA / "patients/BENCH_FAULT/input"
    root.mkdir(parents=True, exist_ok=True)
    good = root / "ok_R1.fastq.gz"
    good.write_bytes(b"")
    bad_vcf = root / "malformed.vcf"
    bad_vcf.write_text("##fileformat=VCFv4.2\nthis is not a VCF record\n")
    cases = [
        ("invalid patient id", "POST", "/api/v1/analyze/vcf", {"patient_id": "../etc", "vcf_path": str(bad_vcf)}, (422,)),
        ("path outside the data root", "POST", "/api/v1/analyze/vcf", {"patient_id": "BENCH_FAULT", "vcf_path": "/etc/passwd"}, (422,)),
        ("missing file", "POST", "/api/v1/analyze/vcf", {"patient_id": "BENCH_FAULT", "vcf_path": str(root / "absent.vcf")}, (422,)),
        ("identical R1 and R2", "POST", "/api/v1/analyze", {"patient_id": "BENCH_FAULT", "fastq_r1": str(good), "fastq_r2": str(good)}, (422,)),
        ("S3 URI rejected", "POST", "/api/v1/analyze/vcf", {"patient_id": "BENCH_FAULT", "vcf_path": "s3://bucket/x.vcf"}, (422,)),
        ("unknown job id", "GET", "/api/v1/jobs/00000000-0000-0000-0000-000000000000", None, (404,)),
        ("empty body", "POST", "/api/v1/analyze", {}, (422,)),
    ]
    out = []
    for label, method, path, body, expected in cases:
        r = api.client.request(method, path, json=body)
        out.append({"case": label, "status_code": r.status_code, "expected": list(expected), "passed": r.status_code in expected})
    log("robustness: malformed VCF (must fail cleanly with a message)")
    job = api.wait(api.submit_vcf("BENCH_FAULT", str(bad_vcf)), timeout=120)
    out.append({"case": "malformed VCF job", "status_code": job["status"], "expected": ["failed"],
                "passed": job["status"] == "failed" and bool(job.get("error")), "message": job.get("error")})
    health = api.client.get("/health").status_code
    out.append({"case": "service healthy after faults", "status_code": health, "expected": [200], "passed": health == 200})
    return {"cases": out, "passed": sum(c["passed"] for c in out), "total": len(out)}


# --- Suite: API latency ------------------------------------------------------------------
def suite_latency(api: Api, log: Callable, n: int = 50) -> Dict[str, Any]:
    endpoints = [("GET", "/health"), ("GET", "/api/v1/tools"), ("GET", "/api/v1/jobs?limit=50"), ("GET", "/api/v1/models/biogpt")]
    out = []
    for method, path in endpoints:
        log(f"latency: {method} {path} × {n}")
        api.client.request(method, path)  # warm-up
        times, errors = [], 0
        for _ in range(n):
            t0 = time.perf_counter()
            r = api.client.request(method, path)
            times.append((time.perf_counter() - t0) * 1000)
            errors += r.status_code >= 400
        out.append({"endpoint": f"{method} {path}", "n": n, "errors": errors,
                    "p50_ms": pct(times, 0.5), "p95_ms": pct(times, 0.95), "p99_ms": pct(times, 0.99),
                    "mean_ms": round(statistics.fmean(times), 2)})
    return {"endpoints": out}


SUITES = {
    "clinical": suite_clinical,
    "analytical": suite_analytical,
    "reproducibility": suite_reproducibility,
    "orchestration": suite_orchestration,
    "robustness": suite_robustness,
    "latency": suite_latency,
}


def environment(api: Api) -> Dict[str, Any]:
    health = api.client.get("/health").json()
    env = {"backend": api.base, "platform": platform.platform(), "python": platform.python_version(),
           "cpus": os.cpu_count(), "pipeline_backend": health.get("pipeline_backend"),
           "orchestrator": health.get("orchestrator"), "gpus": health.get("gpus"),
           "clinvar": health.get("clinvar"), "version": health.get("version")}
    try:
        env["ram_gb"] = round(int(sh("awk '/MemTotal/{print $2}' /proc/meminfo")) / 1024 / 1024, 1)
    except (RuntimeError, ValueError):
        pass
    return env


def to_markdown(res: Dict[str, Any]) -> str:
    L = [f"# GermlineIQ multi-agent benchmark — {res['started_at']}", "",
         f"Version `{res['version']}` · pipeline `{res['environment'].get('pipeline_backend')}` · "
         f"{res['environment'].get('orchestrator')} · {res['environment'].get('cpus')} CPUs", ""]
    s = res["suites"]
    if "clinical" in s and "metrics" in s["clinical"]:
        m = s["clinical"]["metrics"]
        L += ["## Clinical accuracy — real 1000 Genomes cohort", "",
              f"- Carrier sensitivity: **{m['carrier_sensitivity']}** (95 % CI {m['carrier_sensitivity_ci95']})",
              f"- Control specificity: **{m['control_specificity']}** (95 % CI {m['control_specificity_ci95']})",
              f"- Variant recall: **{m['variant_recall']}**; unexpected confirmed P/LP: **{m['unexpected_confirmed_plp']}**; "
              f"unexpected variants flagged \"to confirm\" (low VAF / QC): {m['unexpected_flagged_to_confirm']}",
              f"- Risk-level accuracy: **{m['risk_level_accuracy']}** (95 % CI {m['risk_level_accuracy_ci95']})",
              f"- End-to-end time (s): median {m['end_to_end_s']['median']}, max {m['end_to_end_s']['max']}", "",
              "| Sample | Expected | Reported | Risk (exp → got) | Sites ≥15x | Time (s) |", "|---|---|---|---|---|---|"]
        for x in s["clinical"]["samples"]:
            L.append(f"| {x['sample']} | {', '.join(x['expected_variants']) or '—'} | {', '.join(x['detected_variants']) or '—'} | "
                     f"{x['expected_risk']} → {x['reported_risk']} | {x['pathogenic_sites_coverage']} | {x['wall_time_s']} |")
        L.append("")
    if "analytical" in s and "pass_only" in s["analytical"]:
        a = s["analytical"]
        L += ["## Analytical validity — GIAB HG001", "", f"{a['region']} ({a['evaluated_bp']:,} bp)", "",
              "| Calls | TP | FP | FN | Precision | Recall | F1 |", "|---|---|---|---|---|---|---|"]
        for k in ("pass_only", "all_calls"):
            r = a[k]
            L.append(f"| {k} | {r['true_positives']} | {r['false_positives']} | {r['false_negatives']} | {r['precision']} | {r['recall']} | {r['f1']} |")
        L.append("")
    if "reproducibility" in s:
        L += ["## Reproducibility", ""] + [f"- {c['case']}: {c['runs']} runs, identical reports: **{c['identical_reports']}**, identical statistics: **{c['identical_statistics']}**" for c in s["reproducibility"]["cases"]] + [""]
    if "orchestration" in s:
        o = s["orchestration"]
        L += ["## Orchestration", "", f"- Same plan: **{o['same_plan']}**, same clinical report: **{o['same_clinical_report']}**"]
        for k in ("deterministic", "mistral"):
            L.append(f"- {k}: total median {o[k]['total_s']['median']} s, orchestration overhead median {o[k]['overhead_s']['median']} s")
        L.append("")
    if "robustness" in s:
        r = s["robustness"]
        L += ["## Robustness (fault injection)", "", f"{r['passed']}/{r['total']} cases handled correctly", ""]
        L += [f"- {'✔' if c['passed'] else '✘'} {c['case']} → {c['status_code']}" for c in r["cases"]] + [""]
    if "latency" in s:
        L += ["## API latency", "", "| Endpoint | p50 ms | p95 ms | p99 ms |", "|---|---|---|---|"]
        L += [f"| {e['endpoint']} | {e['p50_ms']} | {e['p95_ms']} | {e['p99_ms']} |" for e in s["latency"]["endpoints"]] + [""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suites", default="all", help="comma-separated: " + ",".join(SUITES) + " or all")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "results")
    args = ap.parse_args()
    names = list(SUITES) if args.suites == "all" else [x.strip() for x in args.suites.split(",")]
    api = Api()
    log = lambda m: print(f"[{datetime.now():%H:%M:%S}] {m}", file=sys.stderr, flush=True)  # noqa: E731
    res: Dict[str, Any] = {"version": BENCHMARK_VERSION, "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "environment": environment(api), "suites": {}}
    for n in names:
        t0 = time.time()
        try:
            res["suites"][n] = SUITES[n](api, log)
        except Exception as e:  # a failing suite is reported, the others still run
            res["suites"][n] = {"error": f"{type(e).__name__}: {e}"}
        res["suites"][n]["suite_duration_s"] = round(time.time() - t0, 1)
    res["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = res["started_at"].replace(":", "").replace("-", "")
    (args.out / f"multiagent_{stamp}.json").write_text(json.dumps(res, indent=2))
    (args.out / f"multiagent_{stamp}.md").write_text(to_markdown(res))
    latest = DATA / "benchmarks"
    latest.mkdir(parents=True, exist_ok=True)
    # merge with the previous latest so that running one suite keeps the others
    prev = json.loads((latest / "latest.json").read_text()) if (latest / "latest.json").is_file() else {"suites": {}}
    merged = {**res, "suites": {**prev.get("suites", {}), **res["suites"]}}
    (latest / "latest.json").write_text(json.dumps(merged, indent=2))
    (latest / "latest.md").write_text(to_markdown(merged))
    print(to_markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
