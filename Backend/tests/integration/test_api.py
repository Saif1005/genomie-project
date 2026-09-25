"""API de bout en bout : VCF → annotation → analyse → risque → rapport (agents et moteur réels)."""

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import FIXTURES

client = TestClient(app)


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    clinvar = root / "reference" / "clinvar" / "clinvar_GRCh38.vcf"
    clinvar.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "clinvar_mini.vcf", clinvar)
    monkeypatch.setenv("CLINVAR_VCF", str(clinvar))
    return root


def deposit(root: Path, patient: str, fixture: str) -> str:
    dest = root / "patients" / patient / "input" / fixture
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / fixture, dest)
    return str(dest)


def wait(job_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} non terminé")


def run_vcf(root, patient, fixture):
    r = client.post("/api/v1/analyze/vcf", json={"patient_id": patient, "vcf_path": deposit(root, patient, fixture)})
    assert r.status_code == 202, r.text
    assert r.json()["plan"] == ["variant_annotation", "vcf_analysis", "prediction", "report"]
    return wait(r.json()["job_id"])


def test_health_describes_local_server(data_root):
    data = client.get("/health").json()
    assert data["status"] == "ok" and data["deployment_mode"] == "local"
    assert data["clinvar"]["available"] is True
    assert "BRCA1" in data["panel"]["germline_genes"]


def test_tools_endpoint_exposes_registry():
    tools = {t["name"]: t for t in client.get("/api/v1/tools").json()}
    assert tools["genomic_pipeline"]["gpu"] == "parabricks"
    assert tools["llm_training"]["critical"] is False


def test_brca1_vcf_gives_high_risk_report(data_root):
    job = run_vcf(data_root, "P1", "patient_brca1_high.vcf")
    assert job["status"] == "completed", job["error"]
    assert job["steps_completed"] == ["variant_annotation", "vcf_analysis", "prediction", "report"]

    report = client.get(f"/api/v1/jobs/{job['job_id']}/report").json()
    pred = report["clinical_prediction"]
    assert pred["risk_level"] == "HIGH"
    assert pred["decision_method"] == "zaynb-rules-v1"
    assert "BRCA1" in pred["clinical_summary"] and "rs80357906" in pred["clinical_summary"]
    variant = report["genomic_findings"]["pathogenic_variants_detected"][0]
    assert variant["gatk_metrics"] == {"QUAL": 812.6, "DP": 42, "VAF": 0.476}
    assert variant["zygosity"] == "heterozygous" and variant["review_stars"] == 3
    assert len(report["genomic_findings"]["vus_detected"]) == 1
    repro = report["reproducibility"]
    assert repro["annotation_version"] == "2026-09-01" and len(repro["input_sha256"]) == 64

    out = data_root / "patients" / "P1" / "output"
    assert (out / "annotated_variants.json").is_file() and (out / "panel_analysis.json").is_file()
    assert (out / f"{report['report_id']}.json").is_file()


def test_same_input_gives_identical_clinical_content(data_root):
    reports = []
    for patient in ("P2", "P2"):
        job = run_vcf(data_root, patient, "patient_brca1_high.vcf")
        reports.append(client.get(f"/api/v1/jobs/{job['job_id']}/report").json())
    for r in reports:
        r.pop("generated_at")
        r["system_metrics"].pop("execution_time_seconds")
    assert reports[0] == reports[1]


def test_low_vaf_variant_is_indeterminate(data_root):
    job = run_vcf(data_root, "P3", "patient_brca2_lowvaf.vcf")
    report = client.get(f"/api/v1/jobs/{job['job_id']}/report").json()
    assert report["clinical_prediction"]["risk_level"] == "INDETERMINATE"
    assert report["genomic_findings"]["variants_to_confirm"][0]["gene"] == "BRCA2"


def test_unannotated_vcf_without_clinvar_fails_explicitly(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setenv("CLINVAR_VCF", str(root / "absent.vcf.gz"))
    job = run_vcf(root, "P4", "patient_brca1_high.vcf")
    assert job["status"] == "failed"
    assert "ClinVar" in job["error"]
    assert client.get(f"/api/v1/jobs/{job['job_id']}/report").status_code == 409


def test_legacy_field_names_still_accepted(data_root):
    r = client.post("/api/v1/analyze/vcf", json={"patient_id": "P5", "vcf_s3": deposit(data_root, "P5", "patient_negative.vcf")})
    assert r.status_code == 202
    assert wait(r.json()["job_id"])["status"] == "completed"


@pytest.mark.parametrize("bad", ["s3://bucket/P1/variants.vcf", "/etc/passwd", "patients/../../x.vcf"])
def test_rejects_paths_outside_data_root(data_root, bad):
    assert client.post("/api/v1/analyze/vcf", json={"patient_id": "P1", "vcf_path": bad}).status_code == 422


def test_rejects_identical_fastq(data_root):
    fq = data_root / "patients" / "P1" / "input" / "R1.fastq.gz"
    fq.parent.mkdir(parents=True)
    fq.write_text("@r\nA\n+\nI\n")
    r = client.post("/api/v1/analyze", json={"patient_id": "P1", "fastq_r1": str(fq), "fastq_r2": str(fq)})
    assert r.status_code == 422


def test_upload_ignores_client_supplied_directories(data_root, monkeypatch):
    from app import jobs as J

    submitted = []
    monkeypatch.setattr(J.JobService, "submit_upload", lambda self, job_id, pid, files, train: submitted.append(files))
    files = {
        "fastq_r1": ("../../../etc/evil_R1.fastq.gz", b"@r\nA\n+\nI\n"),
        "fastq_r2": ("sample_R2.fastq.gz", b"@r\nA\n+\nI\n"),
    }
    r = client.post("/api/v1/analyze/upload", data={"patient_id": "P6"}, files=files)
    assert r.status_code == 202, r.text
    r1 = submitted[0][0]
    assert r1.name == "evil_R1.fastq.gz"
    assert str(r1).startswith(str(data_root / "tmp" / "work" / "uploads"))


def test_job_state_survives_restart(data_root):
    from app.jobs import JobStore

    job = run_vcf(data_root, "P7", "patient_negative.vcf")
    store = JobStore(data_root / "tmp" / "jobs")
    assert store.get(job["job_id"])["status"] == "completed"

    running = store.create("P8", "vcf", [])
    store.update(running, status="running")
    reloaded = JobStore(data_root / "tmp" / "jobs").get(running)
    assert reloaded["status"] == "failed" and "redémarrage" in reloaded["error"]
    assert json.loads((data_root / "tmp" / "jobs" / f"{running}.json").read_text())["status"] == "failed"


def test_smoke_vcf_of_the_deployment_scripts(tmp_path, monkeypatch):
    """Même VCF que scripts/smoke_test.sh, sans base ClinVar locale (annotation embarquée)."""
    root = tmp_path / "data"
    monkeypatch.setenv("CLINVAR_VCF", str(root / "absent.vcf.gz"))
    smoke = Path(__file__).resolve().parents[3] / "scripts" / "testdata" / "smoke_brca.vcf"
    dest = root / "patients" / "SMOKE001" / "input" / "smoke_brca.vcf"
    dest.parent.mkdir(parents=True)
    shutil.copy(smoke, dest)
    r = client.post("/api/v1/analyze/vcf", json={"patient_id": "SMOKE001", "vcf_path": str(dest)})
    job = wait(r.json()["job_id"])
    assert job["status"] == "completed", job["error"]
    report = client.get(f"/api/v1/jobs/{job['job_id']}/report").json()
    assert report["clinical_prediction"]["risk_level"] == "HIGH"
    assert report["genomic_findings"]["identified_pathogenic_genes"] == ["BRCA1", "BRCA2"]
    assert report["reproducibility"]["annotation_source"] == "vcf-embedded-clinvar"
