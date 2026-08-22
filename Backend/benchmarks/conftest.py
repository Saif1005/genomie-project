"""
conftest.py — Fixtures pytest pour les benchmarks.

Permet d'executer les benchmarks via pytest avec des seuils SLA
comme criteres de reussite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.config import BenchmarkConfig


@pytest.fixture(scope="session")
def bench_config() -> BenchmarkConfig:
    """Configuration de benchmark partagee pour toute la session pytest."""
    cfg = BenchmarkConfig()
    cfg.n_iterations = 20
    cfg.n_warmup = 3
    return cfg


@pytest.fixture(scope="session")
def base_url(bench_config: BenchmarkConfig) -> str:
    return bench_config.base_url
