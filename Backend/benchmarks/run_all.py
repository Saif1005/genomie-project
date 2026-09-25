"""
run_all.py - Point d'entree unique pour tous les benchmarks.

Usage:
    python -m benchmarks.run_all
    python -m benchmarks.run_all --suite latency --iterations 50
    python -m benchmarks.run_all --url http://mon-serveur:8000 --suite all
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig
from benchmarks.reporter import BenchmarkReporter

SUITES = ["latency", "load", "pipeline", "all"]


def run_all(cfg: BenchmarkConfig, suite: str = "all") -> None:
    reporter = BenchmarkReporter(cfg)
    start = time.perf_counter()

    print(f"""
+--------------------------------------------------------------+
|      ZAYNB GENOMIC BACKEND  -  SUITE DE BENCHMARKS          |
|                   Projet de Doctorat                         |
+--------------------------------------------------------------+

  Serveur  : {cfg.base_url}
  Suite    : {suite}
  Iterations : {cfg.n_iterations}  (+{cfg.n_warmup} warm-up)
  Workers  : {cfg.n_workers}
  Resultats: {cfg.results_dir}
""")

    if suite in ("latency", "all"):
        print("\n" + "─" * 60)
        print("  [1/3] BENCHMARK DE LATENCE SEQUENTIELLE")
        print("─" * 60)
        from benchmarks.latency_benchmark import run_latency_benchmark
        latency_stats = run_latency_benchmark(cfg)
        reporter.save(latency_stats, benchmark_type="latency")
        reporter.print_summary(latency_stats)

    if suite in ("load", "all"):
        print("\n" + "─" * 60)
        print("  [2/3] TEST DE CHARGE CONCURRENT")
        print("─" * 60)
        from benchmarks.load_test import run_load_test
        load_stats_map = run_load_test(cfg)
        reporter.save(list(load_stats_map.values()), benchmark_type="load")

    if suite in ("pipeline", "all"):
        print("\n" + "─" * 60)
        print("  [3/3] BENCHMARK PIPELINE BOUT-EN-BOUT")
        print("─" * 60)
        from benchmarks.pipeline_benchmark import run_pipeline_benchmark
        run_pipeline_benchmark(cfg, n_runs=3)

    total = time.perf_counter() - start
    print(f"\n  Suite '{suite}' terminee en {total:.1f}s")
    print(f"  Rapports : {Path(cfg.results_dir).resolve()}\n")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Runner de benchmarks - Zaynb Genomic Backend",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python -m benchmarks.run_all
  python -m benchmarks.run_all --url http://localhost:8000 --suite latency
  python -m benchmarks.run_all --suite load --workers 20
  python -m benchmarks.run_all --suite all --iterations 50
        """,
    )
    p.add_argument("--url", default=None)
    p.add_argument("--suite", choices=SUITES, default="all")
    p.add_argument("--iterations", type=int, default=None)
    p.add_argument("--warmup", type=int, default=None)
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--output-dir", default=None)
    p.add_argument("--no-html", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    cfg = BenchmarkConfig()
    if args.url:
        cfg.base_url = args.url
    if args.iterations:
        cfg.n_iterations = args.iterations
    if args.warmup:
        cfg.n_warmup = args.warmup
    if args.workers:
        cfg.n_workers = args.workers
    if args.timeout:
        cfg.request_timeout = args.timeout
    if args.output_dir:
        cfg.results_dir = args.output_dir
    if args.no_html and "html" in cfg.report_format:
        cfg.report_format = [f for f in cfg.report_format if f != "html"]
    run_all(cfg, suite=args.suite)
