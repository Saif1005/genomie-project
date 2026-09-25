"""Configuration commune des tests : environnement isolé, aucune dépendance réseau ou GPU."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """Chaque test tourne en mode local sur une racine de données temporaire."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CLINVAR_VCF", str(tmp_path / "data" / "reference" / "clinvar" / "absent.vcf.gz"))
    monkeypatch.setenv("SKIP_BIOGPT", "true")
    monkeypatch.setenv("ORCHESTRATOR_DETERMINISTIC", "true")
    yield
