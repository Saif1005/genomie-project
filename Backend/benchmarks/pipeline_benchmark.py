"""
pipeline_benchmark.py - Mesure bout-en-bout (soumission -> polling -> resultat).

Usage:
    python -m benchmarks.pipeline_benchmark --runs 5
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig, config as default_config


@dataclass
class PipelineRun:
    patient_id: str
    job_id: Optional[str] = None
    submit_latency_ms: float = 0.0
    polling_duration_s: float = 0.0
    total_duration_s: float = 0.0
    final_status: str = "unknown"
    n_polls: int = 0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.final_status == "completed"


def _submit_vcf_job(client, base_url, patient_id, vcf_s3):
    t0 = time.perf_counter()
    resp = client.post(
        f"{base_url}/api/v1/analyze/vcf",
        json={"patient_id": patient_id, "vcf_s3": vcf_s3},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    if resp.status_code not in (200, 202):
        return None, elapsed_ms
    return resp.json().get("job_id"), elapsed_ms


def _poll_until_done(client, base_url, job_id, poll_interval_s=2.0, max_wait_s=300.0):
    t0 = time.perf_counter()
    n_polls = 0
    final_status = "unknown"
    while (time.perf_counter() - t0) < max_wait_s:
        try:
            resp = client.get(f"{base_url}/api/v1/jobs/{job_id}")
            n_polls += 1
            if resp.status_code == 200:
                status = resp.json().get("status", "unknown")
                if status in ("completed", "failed"):
                    final_status = status
                    break
                logger.debug(f"job={job_id} status={status} elapsed={time.perf_counter()-t0:.1f}s")
        except Exception as exc:
            logger.warning(f"Poll error: {exc}")
        time.sleep(poll_interval_s)
    return final_status, n_polls, time.perf_counter() - t0


def run_single_pipeline(base_url, patient_id, vcf_s3, timeout=30.0) -> PipelineRun:
    run = PipelineRun(patient_id=patient_id)
    t_total = time.perf_counter()
    with httpx.Client(timeout=timeout) as client:
        try:
            job_id, submit_ms = _submit_vcf_job(client, base_url, patient_id, vcf_s3)
        except Exception as exc:
            run.error = str(exc)
            run.total_duration_s = time.perf_counter() - t_total
            return run
        run.job_id = job_id
        run.submit_latency_ms = submit_ms
        if not job_id:
            run.error = "job_id manquant"
            run.total_duration_s = time.perf_counter() - t_total
            return run
        logger.info(f"Job soumis : {job_id} (soumission: {submit_ms:.1f}ms)")
        status, n_polls, poll_s = _poll_until_done(client, base_url, job_id)
        run.polling_duration_s = poll_s
        run.final_status = status
        run.n_polls = n_polls
    run.total_duration_s = time.perf_counter() - t_total
    logger.info(f"Termine : status={status} polls={n_polls} total={run.total_duration_s:.1f}s")
    return run


def run_pipeline_benchmark(cfg: Optional[BenchmarkConfig] = None, n_runs: int = 5):
    cfg = cfg or default_config
    print(f"\n{'=' * 60}")
    print(f"  BENCHMARK PIPELINE COMPLET ({n_runs} runs)")
    print(f"  POST /api/v1/analyze/vcf + polling GET /api/v1/jobs/")
    print(f"{'=' * 60}\n")

    runs: List[PipelineRun] = []
    for i in range(n_runs):
        patient_id = f"BENCH-PIPE-{i:04d}"
        logger.info(f"Run {i+1}/{n_runs} - patient={patient_id}")
        run = run_single_pipeline(cfg.base_url, patient_id, cfg.test_vcf_s3, cfg.request_timeout)
        runs.append(run)
        print(
            f"  Run {i+1:2d} | job={str(run.job_id or 'N/A')[:8]} | "
            f"submit={run.submit_latency_ms:.1f}ms | "
            f"total={run.total_duration_s:.1f}s | status={run.final_status}"
        )

    submit_ms = sorted(r.submit_latency_ms for r in runs)
    total_s = sorted(r.total_duration_s for r in runs)
    n_success = sum(1 for r in runs if r.success)

    def p95(data):
        idx = int(0.95 * (len(data) - 1))
        return data[idx] if data else 0.0

    print(f"\n{'─' * 60}")
    print(f"  Succes : {n_success}/{len(runs)}")
    print(f"  Soumission  : moy={sum(submit_ms)/len(submit_ms):.1f}ms  p95={p95(submit_ms):.1f}ms")
    print(f"  Total pipe  : moy={sum(total_s)/len(total_s):.1f}s   p95={p95(total_s):.1f}s")
    print(f"{'─' * 60}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Benchmark pipeline bout-en-bout")
    p.add_argument("--url", default=None)
    p.add_argument("--runs", type=int, default=5)
    args = p.parse_args()
    cfg = BenchmarkConfig()
    if args.url:
        cfg.base_url = args.url
    run_pipeline_benchmark(cfg, n_runs=args.runs)
