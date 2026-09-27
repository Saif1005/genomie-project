"""Shared test configuration: isolated environment, no network or GPU dependency."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Each test runs in local mode on a temporary data root."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CLINVAR_VCF", str(tmp_path / "data" / "reference" / "clinvar" / "absent.vcf.gz"))
    monkeypatch.setenv("SKIP_BIOGPT", "true")
    monkeypatch.setenv("ORCHESTRATOR_DETERMINISTIC", "true")
    yield
