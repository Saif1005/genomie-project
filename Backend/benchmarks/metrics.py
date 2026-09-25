"""metrics.py - Calcul des metriques de latence (percentiles, stats)."""

from __future__ import annotations
import math
import time
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RequestResult:
    latency_ms: float
    status_code: int
    success: bool
    error: Optional[str] = None


@dataclass
class LatencyStats:
    endpoint: str
    method: str
    n_total: int
    n_success: int
    n_error: int
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p75_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    std_ms: float
    throughput_rps: float
    total_duration_s: float
    sla_p50_ok: bool = True
    sla_p95_ok: bool = True
    sla_p99_ok: bool = True
    sla_error_rate_ok: bool = True

    @property
    def error_rate_pct(self) -> float:
        return (self.n_error / self.n_total * 100) if self.n_total else 0.0

    @property
    def sla_pass(self) -> bool:
        return all([self.sla_p50_ok, self.sla_p95_ok, self.sla_p99_ok, self.sla_error_rate_ok])

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "n_total": self.n_total,
            "n_success": self.n_success,
            "n_error": self.n_error,
            "error_rate_pct": round(self.error_rate_pct, 2),
            "latency_ms": {
                "min": round(self.min_ms, 2),
                "mean": round(self.mean_ms, 2),
                "median_p50": round(self.median_ms, 2),
                "p75": round(self.p75_ms, 2),
                "p90": round(self.p90_ms, 2),
                "p95": round(self.p95_ms, 2),
                "p99": round(self.p99_ms, 2),
                "max": round(self.max_ms, 2),
                "std": round(self.std_ms, 2),
            },
            "throughput_rps": round(self.throughput_rps, 2),
            "total_duration_s": round(self.total_duration_s, 3),
            "sla": {
                "p50_ok": self.sla_p50_ok,
                "p95_ok": self.sla_p95_ok,
                "p99_ok": self.sla_p99_ok,
                "error_rate_ok": self.sla_error_rate_ok,
                "pass": self.sla_pass,
            },
        }


def _percentile(sorted_data: List[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    rank = pct / 100.0 * (n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_data[lower] * (1 - frac) + sorted_data[upper] * frac


def compute_stats(
    endpoint: str,
    method: str,
    results: List[RequestResult],
    total_duration_s: float,
    sla_p50_ms: float = 200,
    sla_p95_ms: float = 500,
    sla_p99_ms: float = 1000,
    sla_error_rate_pct: float = 1.0,
) -> LatencyStats:
    latencies = sorted(r.latency_ms for r in results)
    successes = [r for r in results if r.success]
    errors = [r for r in results if not r.success]
    n = len(latencies)
    if n == 0:
        raise ValueError("Liste de resultats vide.")
    mean = sum(latencies) / n
    variance = sum((x - mean) ** 2 for x in latencies) / n
    std = math.sqrt(variance)
    p50 = _percentile(latencies, 50)
    p75 = _percentile(latencies, 75)
    p90 = _percentile(latencies, 90)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    error_rate = len(errors) / n * 100
    return LatencyStats(
        endpoint=endpoint,
        method=method,
        n_total=n,
        n_success=len(successes),
        n_error=len(errors),
        min_ms=latencies[0],
        max_ms=latencies[-1],
        mean_ms=mean,
        median_ms=p50,
        p75_ms=p75,
        p90_ms=p90,
        p95_ms=p95,
        p99_ms=p99,
        std_ms=std,
        throughput_rps=n / total_duration_s if total_duration_s > 0 else 0.0,
        total_duration_s=total_duration_s,
        sla_p50_ok=p50 <= sla_p50_ms,
        sla_p95_ok=p95 <= sla_p95_ms,
        sla_p99_ok=p99 <= sla_p99_ms,
        sla_error_rate_ok=error_rate <= sla_error_rate_pct,
    )
