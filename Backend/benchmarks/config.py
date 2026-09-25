"""config.py - Configuration centrale des benchmarks."""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class BenchmarkConfig:
    base_url: str = field(
        default_factory=lambda: os.getenv("BENCH_BASE_URL", "http://localhost:8000")
    )
    n_iterations: int = field(
        default_factory=lambda: int(os.getenv("BENCH_ITERATIONS", "30"))
    )
    n_warmup: int = field(
        default_factory=lambda: int(os.getenv("BENCH_WARMUP", "3"))
    )
    n_workers: int = field(
        default_factory=lambda: int(os.getenv("BENCH_WORKERS", "10"))
    )
    request_timeout: float = field(
        default_factory=lambda: float(os.getenv("BENCH_TIMEOUT", "30.0"))
    )
    test_patient_id: str = "BENCH-PATIENT-001"
    test_s3_r1: str = "s3://zaynb-input/bench/sample_R1.fastq.gz"
    test_s3_r2: str = "s3://zaynb-input/bench/sample_R2.fastq.gz"
    test_vcf_s3: str = "s3://zaynb-input/bench/sample.g.vcf.gz"
    sla_p50_ms: float = field(
        default_factory=lambda: float(os.getenv("BENCH_SLA_P50", "200"))
    )
    sla_p95_ms: float = field(
        default_factory=lambda: float(os.getenv("BENCH_SLA_P95", "500"))
    )
    sla_p99_ms: float = field(
        default_factory=lambda: float(os.getenv("BENCH_SLA_P99", "1000"))
    )
    sla_error_rate_pct: float = field(
        default_factory=lambda: float(os.getenv("BENCH_SLA_ERROR_RATE", "1.0"))
    )
    results_dir: str = field(
        default_factory=lambda: os.getenv("BENCH_RESULTS_DIR", "benchmarks/results")
    )
    report_format: List[str] = field(default_factory=lambda: ["json", "csv", "html"])


config = BenchmarkConfig()
