"""
latency_benchmark.py — Benchmark sequentiel de latence par endpoint.

"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx
from loguru import logger

# Ajoute la racine du projet au PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig, config as default_config
from benchmarks.metrics import LatencyStats, RequestResult, compute_stats
from benchmarks.reporter import BenchmarkReporter


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de requetes
# ─────────────────────────────────────────────────────────────────────────────

def _timed_request(
    client: httpx.Client,
    method: str,
    url: str,
    **kwargs,
) -> RequestResult:
    """Execute une requete et retourne le resultat avec la latence."""
    t0 = time.perf_counter()
    try:
        resp = client.request(method, url, **kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        success = resp.status_code in (200, 201, 202)
        return RequestResult(
            latency_ms=elapsed_ms,
            status_code=resp.status_code,
            success=success,
            error=None if success else f"HTTP {resp.status_code}",
        )
    except httpx.TimeoutException as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return RequestResult(
            latency_ms=elapsed_ms,
            status_code=0,
            success=False,
            error=f"Timeout: {exc}",
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return RequestResult(
            latency_ms=elapsed_ms,
            status_code=0,
            success=False,
            error=str(exc),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Definition des scenarios de benchmark
# ─────────────────────────────────────────────────────────────────────────────

def _build_scenarios(cfg: BenchmarkConfig) -> List[Dict]:
    """
    Retourne la liste des scenarios (endpoint, methode, payload).
    Chaque scenario correspond a un appel API distinct.
    """
    base = cfg.base_url.rstrip("/")

    return [
        # ------------------------------------------------------------------
        # 1. Health check — endpoint le plus leger possible
        # ------------------------------------------------------------------
        {
            "name": "health_check",
            "label": "GET /health",
            "method": "GET",
            "url": f"{base}/health",
            "kwargs": {},
        },
        # ------------------------------------------------------------------
        # 2. Analyse FASTQ (soumission de job — 202 attendu)
        # ------------------------------------------------------------------
        {
            "name": "analyze_fastq",
            "label": "POST /api/v1/analyze",
            "method": "POST",
            "url": f"{base}/api/v1/analyze",
            "kwargs": {
                "json": {
                    "patient_id": cfg.test_patient_id,
                    "s3_uri_r1": cfg.test_s3_r1,
                    "s3_uri_r2": cfg.test_s3_r2,
                }
            },
        },
        # ------------------------------------------------------------------
        # 3. Analyse VCF (soumission de job — 202 attendu)
        # ------------------------------------------------------------------
        {
            "name": "analyze_vcf",
            "label": "POST /api/v1/analyze/vcf",
            "method": "POST",
            "url": f"{base}/api/v1/analyze/vcf",
            "kwargs": {
                "json": {
                    "patient_id": cfg.test_patient_id,
                    "vcf_s3": cfg.test_vcf_s3,
                }
            },
        },
        # ------------------------------------------------------------------
        # 4. Chat assistant IA
        # ------------------------------------------------------------------
        {
            "name": "assistant_chat",
            "label": "POST /api/v1/assistant/chat",
            "method": "POST",
            "url": f"{base}/api/v1/assistant/chat",
            "kwargs": {
                "json": {
                    "message": "Quel est le statut du dernier job?",
                    "history": [],
                    "context": {},
                }
            },
        },
        # ------------------------------------------------------------------
        # 5. Statut d'un job inexistant (404 => erreur controlee)
        # ------------------------------------------------------------------
        {
            "name": "job_status_404",
            "label": "GET /api/v1/jobs/{id} (404)",
            "method": "GET",
            "url": f"{base}/api/v1/jobs/00000000-0000-0000-0000-000000000000",
            "kwargs": {},
            "expected_codes": [404],   # 404 est attendu → success=True
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Runner principal
# ─────────────────────────────────────────────────────────────────────────────

def run_latency_benchmark(
    cfg: Optional[BenchmarkConfig] = None,
    scenarios: Optional[List[Dict]] = None,
) -> List[LatencyStats]:
    """
    Benchmarks sequentiels: chaque endpoint est appele N fois,
    les N_warmup premieres requetes sont ignorees.

    Returns
    -------
    List[LatencyStats]
        Une LatencyStats par endpoint, triee par p95 decroissant.
    """
    cfg = cfg or default_config
    scenarios = scenarios or _build_scenarios(cfg)

    all_stats: List[LatencyStats] = []

    with httpx.Client(timeout=cfg.request_timeout) as client:
        for scenario in scenarios:
            name = scenario["name"]
            label = scenario["label"]
            method = scenario["method"]
            url = scenario["url"]
            kwargs = scenario.get("kwargs", {})
            expected_codes = scenario.get("expected_codes", [200, 201, 202])

            logger.info(f"[{label}] Debut du benchmark ({cfg.n_warmup} warm-up + {cfg.n_iterations} iterations)")

            # --- Warm-up ---
            for w in range(cfg.n_warmup):
                _timed_request(client, method, url, **kwargs)
                logger.debug(f"  warmup {w + 1}/{cfg.n_warmup}")

            # --- Mesures ---
            results: List[RequestResult] = []
            t_start = time.perf_counter()

            for i in range(cfg.n_iterations):
                r = _timed_request(client, method, url, **kwargs)
                # Ajuste le succes si on a des codes attendus personnalises
                if expected_codes and r.status_code in expected_codes:
                    r.success = True
                    r.error = None
                results.append(r)
                logger.debug(
                    f"  [{i + 1:03d}/{cfg.n_iterations}] "
                    f"status={r.status_code} latency={r.latency_ms:.1f}ms"
                )

            total_s = time.perf_counter() - t_start

            stats = compute_stats(
                endpoint=label,
                method=method,
                results=results,
                total_duration_s=total_s,
                sla_p50_ms=cfg.sla_p50_ms,
                sla_p95_ms=cfg.sla_p95_ms,
                sla_p99_ms=cfg.sla_p99_ms,
                sla_error_rate_pct=cfg.sla_error_rate_pct,
            )

            _print_stats(stats)
            all_stats.append(stats)

    return all_stats


def _print_stats(s: LatencyStats) -> None:
    """Affiche un tableau synthetique dans le terminal."""
    sla_badge = "✅ SLA OK" if s.sla_pass else "❌ SLA KO"
    print(
        f"\n{'─' * 60}\n"
        f"  {s.endpoint}\n"
        f"{'─' * 60}\n"
        f"  Requetes : {s.n_total}  |  Succes : {s.n_success}"
        f"  |  Erreurs : {s.n_error} ({s.error_rate_pct:.1f}%)\n"
        f"  Min    : {s.min_ms:>8.1f} ms\n"
        f"  Moyenne: {s.mean_ms:>8.1f} ms\n"
        f"  p50    : {s.median_ms:>8.1f} ms\n"
        f"  p75    : {s.p75_ms:>8.1f} ms\n"
        f"  p90    : {s.p90_ms:>8.1f} ms\n"
        f"  p95    : {s.p95_ms:>8.1f} ms  (SLA: <={s.sla_p95_ms:.0f} ms)\n"
        f"  p99    : {s.p99_ms:>8.1f} ms  (SLA: <={s.sla_p99_ms:.0f} ms)\n"
        f"  Max    : {s.max_ms:>8.1f} ms\n"
        f"  Ecart-type: {s.std_ms:.1f} ms\n"
        f"  Debit  : {s.throughput_rps:.2f} req/s\n"
        f"  {sla_badge}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entree CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark de latence — Zaynb Backend")
    p.add_argument("--url", default=None, help="URL de base du serveur (ex. http://localhost:8000)")
    p.add_argument("--iterations", type=int, default=None, help="Nombre d'iterations par endpoint")
    p.add_argument("--warmup", type=int, default=None, help="Nombre de requetes de warm-up")
    p.add_argument("--no-report", action="store_true", help="Ne pas generer le rapport HTML/JSON/CSV")
    p.add_argument("--output-dir", default=None, help="Dossier de sortie des rapports")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    # Surcharge de la config par les arguments CLI
    cfg = BenchmarkConfig()
    if args.url:
        cfg.base_url = args.url
    if args.iterations:
        cfg.n_iterations = args.iterations
    if args.warmup:
        cfg.n_warmup = args.warmup
    if args.output_dir:
        cfg.results_dir = args.output_dir

    logger.info(f"Benchmark cible : {cfg.base_url}")
    logger.info(f"Iterations : {cfg.n_iterations}  |  Warm-up : {cfg.n_warmup}")

    all_stats = run_latency_benchmark(cfg)

    if not args.no_report:
        reporter = BenchmarkReporter(cfg)
        reporter.save(all_stats, benchmark_type="latency")
        reporter.print_summary(all_stats)
