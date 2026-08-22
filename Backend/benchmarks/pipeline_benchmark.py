"""
pipeline_benchmark.py — Mesure de latence du pipeline genomique complet.

Ce benchmark mesure le temps de traitement de bout-en-bout:
  soumission job → polling → resultat final

Particulierement pertinent pour un doctorat sur les pipelines
genomiques car il mesure la latence reelle perçue par le clinicien.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig, config as default_config
from benchmarks.reporter import BenchmarkReporter


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PipelineRun:
    """Resultat d'une execution complete du pipeline."""
    patient_id: str
    job_id: Optional[str] = None

    # Timing
    submit_latency_ms: float = 0.0    # latence de la requete POST
    polling_duration_s: float = 0.0  # duree totale avant completion
    total_duration_s: float = 0.0    # submit + polling

    # Statut final
    final_status: str = "unknown"
    n_polls: int = 0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.final_status == "completed"


@dataclass
class PipelineStats:
    """Statistiques agregees sur plusieurs runs de pipeline."""
    n_runs: int
    n_success: int
    n_failed: int

    # Temps de soumission (latence HTTP POST)
    submit_min_ms: float
    submit_mean_ms: float
    submit_p95_ms: float
    submit_max_ms: float

    # Temps total pipeline (de la soumission a la completion)
    total_min_s: float
    total_mean_s: float
    total_p95_s: float
    total_max_s: float

    def to_dict(self) -> dict:
        return {
            "n_runs": self.n_runs,
            "n_success": self.n_success,
            "n_failed": self.n_failed,
            "error_rate_pct": round((self.n_failed / self.n_runs * 100) if self.n_runs else 0, 2),
            "submit_latency_ms": {
                "min": round(self.submit_min_ms, 2),
                "mean": round(self.submit_mean_ms, 2),
                "p95": round(self.submit_p95_ms, 2),
                "max": round(self.submit_max_ms, 2),
            },
            "total_pipeline_s": {
                "min": round(self.total_min_s, 2),
                "mean": round(self.total_mean_s, 2),
                "p95": round(self.total_p95_s, 2),
                "max": round(self.total_max_s, 2),
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def _submit_vcf_job(
    client: httpx.Client,
    base_url: str,
    patient_id: str,
    vcf_s3: str,
) -> tuple[Optional[str], float]:
    """Soumet un job VCF et retourne (job_id, latence_ms)."""
    t0 = time.perf_counter()
    resp = client.post(
        f"{base_url}/api/v1/analyze/vcf",
        json={"patient_id": patient_id, "vcf_s3": vcf_s3},
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    if resp.status_code not in (200, 202):
        return None, elapsed_ms
    return resp.json().get("job_id"), elapsed_ms


def _poll_until_done(
    client: httpx.Client,
    base_url: str,
    job_id: str,
    poll_interval_s: float = 2.0,
    max_wait_s: float = 300.0,
) -> tuple[str, int, float]:
    """
    Interroge GET /api/v1/jobs/{job_id} jusqu'a completion ou timeout.

    Returns
    -------
    (final_status, n_polls, elapsed_s)
    """
    t0 = time.perf_counter()
    n_polls = 0
    final_status = "unknown"

    while (time.perf_counter() - t0) < max_wait_s:
        try:
            resp = client.get(f"{base_url}/api/v1/jobs/{job_id}")
            n_polls += 1
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                if status in ("completed", "failed"):
                    final_status = status
                    break
                logger.debug(
                    f"job={job_id} status={status} "
                    f"step={data.get('current_step')} "
                    f"elapsed={time.perf_counter() - t0:.1f}s"
                )
        except Exception as exc:
            logger.warning(f"Poll error: {exc}")

        time.sleep(poll_interval_s)

    return final_status, n_polls, time.perf_counter() - t0


def run_single_pipeline(
    base_url: str,
    patient_id: str,
    vcf_s3: str,
    timeout: float = 30.0,
    poll_interval_s: float = 2.0,
    max_wait_s: float = 300.0,
) -> PipelineRun:
    """Execute une mesure complete du pipeline."""
    run = PipelineRun(patient_id=patient_id)
    t_total_start = time.perf_counter()

    with httpx.Client(timeout=timeout) as client:
        # 1. Soumission
        try:
            job_id, submit_ms = _submit_vcf_job(client, base_url, patient_id, vcf_s3)
        except Exception as exc:
            run.error = str(exc)
            run.total_duration_s = time.perf_counter() - t_total_start
            return run

        run.job_id = job_id
        run.submit_latency_ms = submit_ms

        if not job_id:
            run.error = "job_id manquant dans la reponse"
            run.total_duration_s = time.perf_counter() - t_total_start
            return run

        logger.info(f"Job soumis : {job_id} (latence soumission: {submit_ms:.1f}ms)")

        # 2. Polling jusqu'a completion
        t_poll_start = time.perf_counter()
        final_status, n_polls, poll_s = _poll_until_done(
            client, base_url, job_id,
            poll_interval_s=poll_interval_s,
            max_wait_s=max_wait_s,
        )
        run.polling_duration_s = poll_s
        run.final_status = final_status
        run.n_polls = n_polls

    run.total_duration_s = time.perf_counter() - t_total_start
    logger.info(
        f"Pipeline termine : status={final_status} "
        f"polls={n_polls} total={run.total_duration_s:.1f}s"
    )
    return run


def run_pipeline_benchmark(
    cfg: Optional[BenchmarkConfig] = None,
    n_runs: int = 5,
) -> PipelineStats:
    """
    Lance plusieurs runs de pipeline complets et calcule les stats.

    Note: dans un environnement de test, le pipeline est simule
    (il ne lance pas le vrai GATK Parabricks). Les latences mesurees
    refletent la logique orchestrateur + LangGraph.
    """
    cfg = cfg or default_config

    print(f"\n{'═' * 60}")
    print(f"  BENCHMARK PIPELINE COMPLET ({n_runs} runs)")
    print(f"  Endpoint : POST /api/v1/analyze/vcf + polling")
    print(f"{'═' * 60}\n")

    runs: List[PipelineRun] = []

    for i in range(n_runs):
        patient_id = f"BENCH-PIPE-{i:04d}"
        logger.info(f"Run {i + 1}/{n_runs} — patient={patient_id}")
        run = run_single_pipeline(
            base_url=cfg.base_url,
            patient_id=patient_id,
            vcf_s3=cfg.test_vcf_s3,
            timeout=cfg.request_timeout,
        )
        runs.append(run)
        print(
            f"  Run {i + 1:2d} | job={run.job_id or 'N/A'[:8]} | "
            f"submit={run.submit_latency_ms:.1f}ms | "
            f"total={run.total_duration_s:.1f}s | "
            f"status={run.final_status}"
        )

    # Calcul des stats
    submit_ms = sorted(r.submit_latency_ms for r in runs)
    total_s = sorted(r.total_duration_s for r in runs)
    n_success = sum(1 for r in runs if r.success)

    def _p95(data):
        n = len(data)
        idx = int(0.95 * (n - 1))
        return data[idx] if data else 0.0

    stats = PipelineStats(
        n_runs=len(runs),
        n_success=n_success,
        n_failed=len(runs) - n_success,
        submit_min_ms=min(submit_ms, default=0),
        submit_mean_ms=sum(submit_ms) / len(submit_ms) if submit_ms else 0,
        submit_p95_ms=_p95(submit_ms),
        submit_max_ms=max(submit_ms, default=0),
        total_min_s=min(total_s, default=0),
        total_mean_s=sum(total_s) / len(total_s) if total_s else 0,
        total_p95_s=_p95(total_s),
        total_max_s=max(total_s, default=0),
    )

    print(f"\n{'─' * 60}")
    print(f"  Succes : {n_success}/{len(runs)}")
    print(f"  Latence soumission — moy: {stats.submit_mean_ms:.1f}ms  p95: {stats.submit_p95_ms:.1f}ms")
    print(f"  Temps total pipeline — moy: {stats.total_mean_s:.1f}s   p95: {stats.total_p95_s:.1f}s")
    print(f"{'─' * 60}\n")

    return stats


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Benchmark pipeline complet")
    p.add_argument("--url", default=None)
    p.add_argument("--runs", type=int, default=5)
    args = p.parse_args()

    cfg = BenchmarkConfig()
    if args.url:
        cfg.base_url = args.url

    run_pipeline_benchmark(cfg, n_runs=args.runs)
