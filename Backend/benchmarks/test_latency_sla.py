"""
test_latency_sla.py - Tests pytest avec assertion SLA.

Usage:
    pytest benchmarks/test_latency_sla.py -v
"""

from __future__ import annotations
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from benchmarks.config import BenchmarkConfig
from benchmarks.latency_benchmark import _timed_request
from benchmarks.metrics import compute_stats


@pytest.fixture(scope="session", autouse=True)
def check_server_reachable(bench_config: BenchmarkConfig):
    try:
        resp = httpx.get(f"{bench_config.base_url}/health", timeout=5.0)
        assert resp.status_code == 200
    except Exception as exc:
        pytest.skip(f"Serveur {bench_config.base_url} inaccessible: {exc}")


class TestLatencySLA:

    @pytest.fixture(autouse=True)
    def _setup(self, bench_config: BenchmarkConfig):
        self.cfg = bench_config

    def _run_endpoint(self, method: str, url: str, **kwargs):
        results = []
        with httpx.Client(timeout=self.cfg.request_timeout) as client:
            for _ in range(self.cfg.n_warmup):
                _timed_request(client, method, url, **kwargs)
            t_start = time.perf_counter()
            for _ in range(self.cfg.n_iterations):
                results.append(_timed_request(client, method, url, **kwargs))
            total_s = time.perf_counter() - t_start
        return compute_stats(
            endpoint=url, method=method, results=results, total_duration_s=total_s,
            sla_p50_ms=self.cfg.sla_p50_ms, sla_p95_ms=self.cfg.sla_p95_ms,
            sla_p99_ms=self.cfg.sla_p99_ms, sla_error_rate_pct=self.cfg.sla_error_rate_pct,
        )

    def test_health_p95(self, bench_config):
        stats = self._run_endpoint("GET", f"{bench_config.base_url}/health")
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"GET /health p95={stats.p95_ms:.1f}ms > SLA {bench_config.sla_p95_ms}ms"
        )

    def test_health_error_rate(self, bench_config):
        stats = self._run_endpoint("GET", f"{bench_config.base_url}/health")
        assert stats.error_rate_pct <= bench_config.sla_error_rate_pct

    def test_analyze_vcf_p95(self, bench_config):
        stats = self._run_endpoint(
            "POST", f"{bench_config.base_url}/api/v1/analyze/vcf",
            json={"patient_id": bench_config.test_patient_id, "vcf_s3": bench_config.test_vcf_s3},
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms, (
            f"POST /vcf p95={stats.p95_ms:.1f}ms > SLA {bench_config.sla_p95_ms}ms"
        )

    def test_analyze_fastq_p95(self, bench_config):
        stats = self._run_endpoint(
            "POST", f"{bench_config.base_url}/api/v1/analyze",
            json={
                "patient_id": bench_config.test_patient_id,
                "s3_uri_r1": bench_config.test_s3_r1,
                "s3_uri_r2": bench_config.test_s3_r2,
            },
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms

    def test_assistant_chat_p95(self, bench_config):
        stats = self._run_endpoint(
            "POST", f"{bench_config.base_url}/api/v1/assistant/chat",
            json={"message": "Statut?", "history": [], "context": {}},
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms

    def test_job_status_404_p95(self, bench_config):
        stats = self._run_endpoint(
            "GET",
            f"{bench_config.base_url}/api/v1/jobs/00000000-0000-0000-0000-000000000000",
        )
        assert stats.p95_ms <= bench_config.sla_p95_ms
