"""Pipeline germinal : commandes générées et reprise sur incident (sans Docker ni GPU)."""

from pathlib import Path

import pytest

from src.pipeline import commands as C
from src.pipeline.executor import CommandResult, PipelineError
from src.pipeline.germline import BACKEND_CPU, BACKEND_GPU, GermlinePipeline
from src.pipeline.reference import ReferenceBundle


def test_bwa_alignment_is_deterministic_and_streamed():
    cmd = C.bwa_align_sort("/ref/hg38.fa", "/p/R1.fq.gz", "/p/R2.fq.gz", "/o/raw.bam", "P1", 16)
    assert "-K 100000000" in cmd  # résultat indépendant du nombre de threads
    assert "| samtools sort" in cmd and ".sam" not in cmd
    assert "SM:P1" in cmd


def test_bqsr_uses_every_known_site():
    cmd = C.base_recalibration("/r.fa", "/i.bam", "/o.bam", "/t.table", ["/k1.vcf.gz", "/k2.vcf.gz"])
    assert cmd.count("--known-sites") == 2 and "ApplyBQSR" in cmd


def test_haplotypecaller_is_restricted_to_panel():
    assert "-L /panel.bed" in C.haplotype_caller("/r.fa", "/i.bam", "/o.vcf.gz", "/panel.bed")
    pb = C.pbrun_haplotypecaller("/r.fa", "/i.bam", "/o.vcf", "/panel.bed", "/recal.txt")
    assert pb[pb.index("--interval-file") + 1] == "/panel.bed"
    assert pb[pb.index("--in-recal-file") + 1] == "/recal.txt"


def test_parabricks_fq2bam_builds_bqsr_table_in_one_pass():
    args = C.pbrun_fq2bam("/r.fa", "/R1", "/R2", "/o.bam", "P1", ["/k.vcf.gz"], "/recal.txt", low_memory=True)
    assert args[:2] == ["pbrun", "fq2bam"]
    assert args[args.index("--out-recal-file") + 1] == "/recal.txt"
    assert "--low-memory" in args
    no_sites = C.pbrun_fq2bam("/r.fa", "/R1", "/R2", "/o.bam", "P1", [], None, low_memory=False)
    assert "--knownSites" not in no_sites and "--out-recal-file" not in no_sites


def test_postprocess_normalizes_then_filters_snvs_and_indels_separately():
    script = C.postprocess_script("/r.fa", "/raw.vcf.gz", "/final.vcf.gz", "/w")
    order = [script.index(s) for s in ("LeftAlignAndTrimVariants", "SelectVariants", "VariantFiltration", "MergeVcfs")]
    assert order == sorted(order)
    assert "--split-multi-allelics" in script
    assert "SNP_FS60" in script and "INDEL_FS200" in script


class FakeExecutor:
    """Simule chaque étape en créant les fichiers de sortie attendus ; peut échouer à la demande."""

    def __init__(self, pipeline_ref, fail_on=None):
        self.ran = []
        self.fail_on = fail_on
        self.pipeline_ref = pipeline_ref

    def run(self, command, timeout=None):
        step = next(s for s in self.pipeline_ref["steps"] if s.command == command)
        self.ran.append(step.name)
        if step.name == self.fail_on:
            return CommandResult(1, "", "erreur simulée")
        for out in step.outputs:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(step.name)
        return CommandResult(0, "", "")


@pytest.fixture
def inputs(tmp_path):
    for name in ("R1.fq.gz", "R2.fq.gz", "hg38.fa", "panel.bed"):
        (tmp_path / name).write_text(name)
    return tmp_path


def _pipeline(inputs, backend, fail_on=None):
    ref = ReferenceBundle(fasta=str(inputs / "hg38.fa"), known_sites=())
    holder = {}
    executor = FakeExecutor(holder, fail_on)
    pipeline = GermlinePipeline(backend, executor, ref, str(inputs / "panel.bed"), threads=4)
    out = inputs / "out"
    holder["steps"] = pipeline.steps("P1", str(inputs / "R1.fq.gz"), str(inputs / "R2.fq.gz"), out)
    return pipeline, executor, out


def test_cpu_pipeline_resumes_after_failure(inputs):
    pipeline, executor, out = _pipeline(inputs, BACKEND_CPU, fail_on="haplotypecaller")
    with pytest.raises(PipelineError, match="erreur simulée"):
        pipeline.run("P1", str(inputs / "R1.fq.gz"), str(inputs / "R2.fq.gz"), out)
    assert executor.ran == ["align", "markdup", "finalize_bam", "haplotypecaller"]

    pipeline, executor, out = _pipeline(inputs, BACKEND_CPU)
    result = pipeline.run("P1", str(inputs / "R1.fq.gz"), str(inputs / "R2.fq.gz"), out)
    assert result.steps_resumed == ["align", "markdup", "finalize_bam"]
    assert executor.ran == ["haplotypecaller", "postprocess"]
    assert not (out / "work").exists()  # intermédiaires nettoyés


def test_changed_fastq_invalidates_checkpoints(inputs):
    pipeline, _, out = _pipeline(inputs, BACKEND_GPU)
    pipeline.run("P1", str(inputs / "R1.fq.gz"), str(inputs / "R2.fq.gz"), out)
    (inputs / "R1.fq.gz").write_text("nouveau contenu, taille différente")
    pipeline, executor, out = _pipeline(inputs, BACKEND_GPU)
    pipeline.run("P1", str(inputs / "R1.fq.gz"), str(inputs / "R2.fq.gz"), out)
    assert executor.ran == ["fq2bam", "haplotypecaller", "postprocess"]
