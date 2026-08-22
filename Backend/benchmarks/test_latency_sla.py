"""
test_latency_sla.py — Tests pytest qui valident les SLA de latence.

Ces tests echouent automatiquement si les SLA ne sont pas respectes.
Integrable dans la CI/CD du projet de doctorat.

Usage:
    pytest benchmarks/test_latency_sla.py -v
    pytest benchmarks/test_latency_sla.py -v --tb=short
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig
from benchmarks.latency_benchmark import _build_scenarios, _timed_request, run_latency_benchmark
from benchmarks.metrics import compute_stats


# ─────────────────────────────────────────────────────────────────────────────
# Fixture — verifie que le serveur est accessible
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def check_server_reachable(bench_config: BenchmarkConfig):
    """Saute tous les tests si le serveur n'est pas joignable."""
    try:
        resp = httpx.get(f"{bench_config.base_url}/health", timeout=5.0)
        assert resp.status_code == 200, f"Health check failed: {resp.status_code}"
    except Exception as exc:
        pytest.skip(f"Serveur {bench_config.base_url} inaccessible: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Tests de latence par endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestLatencySLA:
    """Valide les SLA de latence pour chaque endpoint."""

    @pytest.fixture(autouse=True)
    def _setup(self, bench_config: BenchmarkConfig):
        self.cfg = bench_config

    def _run_endpoint(self, method: str, url: str, **kwargs):
        """Helper: execute N requetes et retourne les stats."""
        import time
        results = []

        with httpx.Client(timeout=self.cfg.request_timeout) as client:
            # warm-up
            for _ in range(self.cfg.n_warmup):
                _timed_request(client, method, url, **kwargs)

            t_start = time.perf_counter()
            for _ in range(self.cfg.n_iterations):
                results.append(_timed_request(client, method, url, **kwargs))
            total_s = time.perf_counter() - t_start

        return compute_stats(
            endpoint=url,
            method=method,
            results=results,
            total_duration_s=total_s,
            sla_p50_ms=self.cfg.sla_p50_ms,
            sla_p95_ms=self.cfg.sla_p95_ms,
            sla_p99_ms=self.cfg.sla_p99_ms,
            sla_error_rate_pct=self.cfg.sla_error_rate_pct,
        )

    def test_health_check_p95(self, bench_config):
        """GET /health : p95 < SLA."""
        stats = self._run_endpoint("GET", f"{bench_config.base_url}/health")
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"GET /health p95={stats.p95_ms:.1f}ms depasse le SLA de {bench_config.sla_p95_ms}ms"
        )

    def test_health_check_error_rate(self, bench_config):
        """GET /health : taux d'erreur < SLA."""
        stats = self._run_endpoint("GET", f"{bench_config.base_url}/health")
        assert stats.error_rate_pct <= bench_config.sla_error_rate_pct, (
            f"GET /health error_rate={stats.error_rate_pct:.1f}% depasse le SLA de {bench_config.sla_error_rate_pct}%"
        )

    def test_analyze_vcf_p95(self, bench_config):
        """POST /api/v1/analyze/vcf : p95 < SLA."""
        stats = self._run_endpoint(
            "POST",
            f"{bench_config.base_url}/api/v1/analyze/vcf",
            json={
                "patient_id": bench_config.test_patient_id,
                "vcf_s3": bench_config.test_vcf_s3,
            },
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"POST /vcf p95={stats.p95_ms:.1f}ms depasse le SLA de {bench_config.sla_p95_ms}ms"
        )

    def test_analyze_fastq_p95(self, bench_config):
        """POST /api/v1/analyze : p95 < SLA."""
        stats = self._run_endpoint(
            "POST",
            f"{bench_config.base_url}/api/v1/analyze",
            json={
                "patient_id": bench_config.test_patient_id,
                "s3_uri_r1": bench_config.test_s3_r1,
                "s3_uri_r2": bench_config.test_s3_r2,
            },
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"POST /analyze p95={stats.p95_ms:.1f}ms depasse le SLA de {bench_config.sla_p95_ms}ms"
        )

    def test_assistant_chat_p95(self, bench_config):
        """POST /api/v1/assistant/chat : p95 < SLA."""
        stats = self._run_endpoint(
            "POST",
            f"{bench_config.base_url}/api/v1/assistant/chat",
            json={
                "message": "Statut?",
                "history": [],
                "context": {},
            },
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"POST /chat p95={stats.p95_ms:.1f}ms depasse le SLA de {bench_config.sla_p95_ms}ms"
        )

    def test_job_status_404_p95(self, bench_config):
        """GET /api/v1/jobs/{id} 404 : p95 < SLA."""
        stats = self._run_endpoint(
            "GET",
            f"{bench_config.base_url}/api/v1/jobs/00000000-0000-0000-0000-000000000000",
        )
        # 404 est OK pour cet endpoint — on mesure juste la vitesse
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"GET /jobs/404 p95={stats.p95_ms:.1f}ms depasse le SLA de {bench_config.sla_p95_ms}ms"
        )

    def test_all_endpoints_p99(self, bench_config):
        """Tous les endpoints : p99 < SLA global."""
        all_stats = run_latency_benchmark(bench_config)
        failed = [s for s in all_stats if not s.sla_p99_ok]
        assert not failed, (
            "Endpoints ne respectant pas le SLA p99: "
            + ", ".join(f"{s.endpoint} ({s.p99_ms:.1f}ms)" for s in failed)
        )
