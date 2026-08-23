"""
load_test.py — Test de charge concurrent (workers paralleles).

"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig, config as default_config
from benchmarks.metrics import LatencyStats, RequestResult, compute_stats
from benchmarks.reporter import BenchmarkReporter


# ─────────────────────────────────────────────────────────────────────────────
# Paliers de charge (nombre de workers concurrents)
# ─────────────────────────────────────────────────────────────────────────────
LOAD_LEVELS = [1, 2, 5, 10, 20, 50]


def _worker_request(
    base_url: str,
    timeout: float,
    patient_suffix: int,
) -> RequestResult:
    """
    Une seule requete executee dans un thread independant.
    Utilise un patient_id unique pour eviter les collisions.
    """
    url = f"{base_url}/api/v1/analyze/vcf"
    payload = {
        "patient_id": f"BENCH-LOAD-{patient_suffix:06d}",
        "vcf_s3": "s3://zaynb-input/bench/sample.g.vcf.gz",
    }
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        success = resp.status_code in (200, 202)
        return RequestResult(
            latency_ms=elapsed_ms,
            status_code=resp.status_code,
            success=success,
            error=None if success else f"HTTP {resp.status_code}",
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return RequestResult(latency_ms=elapsed_ms, status_code=0, success=False, error=str(exc))


def run_load_level(
    base_url: str,
    n_workers: int,
    n_requests: int,
    timeout: float,
) -> Tuple[List[RequestResult], float]:
    """
    Soumet n_requests requetes avec n_workers threads simultanes.

    Returns
    -------
    (results, total_duration_s)
    """
    results: List[RequestResult] = []
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {
            executor.submit(_worker_request, base_url, timeout, i): i
            for i in range(n_requests)
        }
        for future in as_completed(futures):
            results.append(future.result())

    total_s = time.perf_counter() - t_start
    return results, total_s


def run_load_test(
    cfg: Optional[BenchmarkConfig] = None,
    load_levels: Optional[List[int]] = None,
) -> Dict[int, LatencyStats]:
    """
    Execute le test de charge a plusieurs niveaux de concurrence.

    Returns
    -------
    Dict[workers -> LatencyStats]
    """
    cfg = cfg or default_config
    levels = load_levels or LOAD_LEVELS

    all_stats: Dict[int, LatencyStats] = {}

    print(f"\n{'═' * 70}")
    print(f"  TEST DE CHARGE — {cfg.base_url}")
    print(f"  Endpoint : POST /api/v1/analyze/vcf")
    print(f"{'═' * 70}")

    for n_workers in levels:
        n_req = max(n_workers * 3, 10)   # au moins 3 requetes par worker
        logger.info(f"Niveau de charge : {n_workers} workers / {n_req} requetes")

        results, total_s = run_load_level(
            base_url=cfg.base_url,
            n_workers=n_workers,
            n_requests=n_req,
            timeout=cfg.request_timeout,
        )

        stats = compute_stats(
            endpoint=f"POST /api/v1/analyze/vcf [{n_workers}w]",
            method="POST",
            results=results,
            total_duration_s=total_s,
            sla_p50_ms=cfg.sla_p50_ms,
            sla_p95_ms=cfg.sla_p95_ms,
            sla_p99_ms=cfg.sla_p99_ms,
            sla_error_rate_pct=cfg.sla_error_rate_pct,
        )
        all_stats[n_workers] = stats

        print(
            f"\n  Workers={n_workers:3d} | Req={n_req:4d} | "
            f"p50={stats.median_ms:6.1f}ms | "
            f"p95={stats.p95_ms:6.1f}ms | "
            f"p99={stats.p99_ms:6.1f}ms | "
            f"RPS={stats.throughput_rps:6.2f} | "
            f"Err={stats.error_rate_pct:.1f}% | "
            f"{'✅' if stats.sla_pass else '❌'}"
        )

    _print_scaling_summary(all_stats)
    return all_stats


def _print_scaling_summary(all_stats: Dict[int, LatencyStats]) -> None:
    """Affiche le tableau de degradation de latence en fonction de la charge."""
    print(f"\n{'─' * 70}")
    print("  SYNTHESE DE SCALABILITE")
    print(f"{'─' * 70}")
    print(f"  {'Workers':>8} | {'p50 (ms)':>10} | {'p95 (ms)':>10} | {'p99 (ms)':>10} | {'RPS':>8} | SLA")
    print(f"  {'─' * 8}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 8}─┼────")
    for n_workers, s in sorted(all_stats.items()):
        badge = "✅" if s.sla_pass else "❌"
        print(
            f"  {n_workers:>8} | {s.median_ms:>10.1f} | {s.p95_ms:>10.1f} | "
            f"{s.p99_ms:>10.1f} | {s.throughput_rps:>8.2f} | {badge}"
        )
    print(f"{'─' * 70}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Test de charge concurrent — Zaynb Backend")
    p.add_argument("--url", default=None)
    p.add_argument("--iterations", type=int, default=None)
    p.add_argument("--levels", nargs="+", type=int, default=None,
                   help="Paliers de workers. Ex: --levels 1 5 10 20")
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--output-dir", default=None)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    cfg = BenchmarkConfig()
    if args.url:
        cfg.base_url = args.url
    if args.iterations:
        cfg.n_iterations = args.iterations
    if args.output_dir:
        cfg.results_dir = args.output_dir

    levels = args.levels or LOAD_LEVELS

    stats_map = run_load_test(cfg, load_levels=levels)

    if not args.no_report:
        stats_list = list(stats_map.values())
        reporter = BenchmarkReporter(cfg)
        reporter.save(stats_list, benchmark_type="load")
