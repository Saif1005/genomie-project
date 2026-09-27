"""VariantCallingAgent: GATK4 CPU fallback when Parabricks fails under automatic selection."""

from types import SimpleNamespace

import pytest

from src.agents import variant_calling as vc
from src.core.agent import AgentError
from src.pipeline.executor import PipelineError
from src.pipeline.germline import BACKEND_CPU, BACKEND_GPU


class _Pipeline:
    runs = []

    def __init__(self, backend, executor, reference, bed):
        self.backend = backend

    def run(self, pid, r1, r2, out_dir):
        _Pipeline.runs.append(self.backend)
        if self.backend == BACKEND_GPU:
            raise PipelineError("fq2bam failed (code 255): OOM")
        return SimpleNamespace(vcf=str(out_dir / "variants.vcf.gz"), bam=str(out_dir / "aligned.bam"), steps_run=[], steps_resumed=[])


@pytest.fixture
def agent(monkeypatch, tmp_path):
    _Pipeline.runs = []
    panel = SimpleNamespace(version="v", write_bed=lambda path, padding: path)
    monkeypatch.setattr(vc, "get_storage", lambda: None)
    monkeypatch.setattr(vc, "patient_output_dir", lambda pid: tmp_path)
    monkeypatch.setattr(vc, "get_panel", lambda: panel)
    monkeypatch.setattr(vc, "panel_padding", lambda: 100)
    monkeypatch.setattr(vc, "paths", lambda: SimpleNamespace(panels_dir=tmp_path))
    monkeypatch.setattr(vc, "resolve_reference", lambda need_bwa_index: SimpleNamespace(fasta="/r.fa", known_sites=()))
    monkeypatch.setattr(vc, "GermlinePipeline", _Pipeline)
    monkeypatch.setattr(vc.VariantCallingAgent, "_local_input", lambda self, uri, work: uri)
    monkeypatch.setattr(vc.VariantCallingAgent, "_alignment_qc", lambda self, *a: {})
    return vc.VariantCallingAgent()


def _select(monkeypatch, requested):
    monkeypatch.setattr(vc, "select_pipeline_backend", lambda: {"requested": requested, "backend": "parabricks", "reason": "GPU"})


CTX = {"patient_id": "P1", "fastq_r1_uri": "/R1", "fastq_r2_uri": "/R2"}


def test_auto_selection_falls_back_to_cpu_when_parabricks_fails(agent, monkeypatch):
    _select(monkeypatch, "auto")
    out = agent.execute(dict(CTX)).data
    assert _Pipeline.runs == [BACKEND_GPU, BACKEND_CPU]
    assert out["pipeline_backend"] == BACKEND_CPU
    assert "fell back to GATK4 on CPU" in out["pipeline_backend_reason"]


def test_forced_parabricks_does_not_fall_back(agent, monkeypatch):
    _select(monkeypatch, "parabricks")
    with pytest.raises(AgentError, match="parabricks"):
        agent.execute(dict(CTX))
    assert _Pipeline.runs == [BACKEND_GPU]
