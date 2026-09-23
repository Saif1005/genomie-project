"""Tests API backend production (app/main.py)."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def aws_mode(monkeypatch):
    """Les tests historiques ci-dessous valident le contrat S3 (DEPLOYMENT_MODE=aws)."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "aws")


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_rejects_invalid_s3():
    r = client.post(
        "/api/v1/analyze",
        json={
            "patient_id": "PATIENT001",
            "s3_uri_r1": "not-a-uri",
            "s3_uri_r2": "s3://bucket/patients/P1/input/R2.fastq.gz",
        },
    )
    assert r.status_code == 422


def test_analyze_rejects_identical_fastq():
    uri = "s3://genomic-cancer-pipeline-input-dev-857281493967/patients/P1/input/R1.fastq.gz"
    r = client.post(
        "/api/v1/analyze",
        json={
            "patient_id": "PATIENT001",
            "s3_uri_r1": uri,
            "s3_uri_r2": uri,
        },
    )
    assert r.status_code == 422


def test_analyze_accepts_valid_payload():
    r = client.post(
        "/api/v1/analyze",
        json={
            "patient_id": "PATIENT001",
            "s3_uri_r1": "s3://genomic-cancer-pipeline-input-dev-857281493967/patients/PATIENT001/input/R1.fastq.gz",
            "s3_uri_r2": "s3://genomic-cancer-pipeline-input-dev-857281493967/patients/PATIENT001/input/R2.fastq.gz",
        },
    )
    assert r.status_code == 202
    data = r.json()
    assert "job_id" in data
    assert data["status"] == "queued"
    assert data["patient_id"] == "PATIENT001"

    status_r = client.get(f"/api/v1/jobs/{data['job_id']}")
    assert status_r.status_code == 200
    assert status_r.json()["job_id"] == data["job_id"]


def test_job_not_found():
    r = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# --- Mode local (serveur on-premise) -------------------------------------


@pytest.fixture
def local_root(monkeypatch, tmp_path):
    monkeypatch.setenv("DEPLOYMENT_MODE", "local")
    monkeypatch.setenv("LOCAL_DATA_ROOT", str(tmp_path))
    submitted = []
    monkeypatch.setattr(main_module, "_submit", lambda fn, *a: submitted.append(a))
    return tmp_path, submitted


def test_local_health_reports_mode(local_root):
    root, _ = local_root
    data = client.get("/health").json()
    assert data["deployment_mode"] == "local"
    assert data["data_root"] == str(root)
    assert data["pipeline_backend"] in ("cpu", "parabricks")


def test_local_vcf_accepts_server_path(local_root):
    root, submitted = local_root
    vcf = root / "patients" / "P1" / "input" / "variants.vcf"
    vcf.parent.mkdir(parents=True)
    vcf.write_text("##fileformat=VCFv4.2\n")
    r = client.post("/api/v1/analyze/vcf", json={"patient_id": "P1", "vcf_s3": str(vcf)})
    assert r.status_code == 202
    assert len(submitted) == 1


def test_local_rejects_s3_and_outside_root(local_root):
    for bad in ("s3://bucket/P1/variants.vcf", "/etc/passwd", "patients/../../x.vcf"):
        r = client.post("/api/v1/analyze/vcf", json={"patient_id": "P1", "vcf_s3": bad})
        assert r.status_code == 422, bad
