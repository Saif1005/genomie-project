"""VariantCallingAgent — FASTQ → filtered germline VCF on the panel (Parabricks GPU or GATK4 CPU)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from config.settings import panel_padding, paths
from src.agents._io import patient_output_dir, write_artifact
from src.core import context as K
from src.core.agent import AgentError, AgentResult, BaseAgent
from src.genomics import GenePanel, QCThresholds, get_panel
from src.genomics.clinvar import ClinVarIndex, default_clinvar_path
from src.pipeline.alignment_qc import compute_alignment_qc
from src.pipeline.executor import LocalExecutor, PipelineError
from src.pipeline.germline import BACKEND_CPU, BACKEND_GPU, GermlinePipeline
from src.pipeline.reference import resolve_reference
from src.storage import StorageError, get_storage
from src.utils.gpu_manager import select_pipeline_backend


class VariantCallingAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("VariantCalling", config)
        self.storage = get_storage()

    def _local_input(self, uri: str, work: Path) -> str:
        try:
            return self.storage.fetch(uri, work / Path(uri).name)
        except StorageError as e:
            raise AgentError(f"FASTQ not accessible: {e}") from e

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        pid = context[K.PATIENT_ID]
        selection = select_pipeline_backend()
        backend = BACKEND_GPU if selection["backend"] == "parabricks" else BACKEND_CPU
        if backend == BACKEND_CPU:
            self.logger.warning(
                f"⚠ GATK4 on CPU ({selection['reason']}): several hours for a genome, "
                "less for an exome; variant calling stays restricted to the panel."
            )
        out_dir = patient_output_dir(pid)
        r1 = self._local_input(context[K.FASTQ_R1_URI], out_dir)
        r2 = self._local_input(context[K.FASTQ_R2_URI], out_dir)

        panel, padding = get_panel(), panel_padding()
        bed = str(panel.write_bed(paths().panels_dir / f"breast_panel_{panel.version}_pad{padding}.bed", padding))
        reason = selection["reason"]
        try:
            reference = resolve_reference(need_bwa_index=backend == BACKEND_CPU)
            result = GermlinePipeline(backend, LocalExecutor(), reference, bed).run(pid, r1, r2, out_dir)
        except PipelineError as e:
            # Automatic selection only: Parabricks can run out of host RAM on large inputs
            # (exome/genome on a small server); GATK4 on CPU gives the same calls, more slowly.
            if backend != BACKEND_GPU or selection["requested"] != "auto":
                raise AgentError(f"Variant calling ({backend}): {e}") from e
            first_line = str(e).split(":", 1)[0][:200]  # e.g. "fq2bam failed (code 255)"
            self.logger.warning(f"⚠ Parabricks failed ({first_line}) — falling back to GATK4 on CPU")
            backend, reason = BACKEND_CPU, f"Parabricks failed ({first_line}); fell back to GATK4 on CPU"
            try:
                reference = resolve_reference(need_bwa_index=True)
                result = GermlinePipeline(backend, LocalExecutor(), reference, bed).run(pid, r1, r2, out_dir)
            except PipelineError as e2:
                raise AgentError(f"Variant calling ({backend}, after Parabricks failure): {e2}") from e2

        self.logger.info(
            f"Filtered VCF: {result.vcf} (steps run {result.steps_run}, resumed {result.steps_resumed})"
        )
        alignment_qc = self._alignment_qc(pid, Path(result.bam), out_dir, panel)
        return AgentResult.ok(**{
            K.VCF_URI: result.vcf,
            K.BAM_URI: result.bam,
            K.PIPELINE_BACKEND: backend,
            K.ALIGNMENT_QC: alignment_qc,
            "pipeline_backend_reason": reason,
            "reference": reference.fasta,
            "known_sites": list(reference.known_sites),
        })

    def _alignment_qc(self, pid: str, bam: Path, out_dir: Path, panel: GenePanel) -> Dict[str, Any]:
        """Mapping rate, duplicates, coverage of ClinVar P/LP sites (alignment_qc.json artifact)."""
        clinvar = default_clinvar_path()
        index = ClinVarIndex.load(clinvar, panel) if clinvar.is_file() else None
        qc = compute_alignment_qc(
            bam,
            out_dir / "duplicate_metrics.txt",
            index,
            panel,
            QCThresholds.from_env().min_depth,
            out_dir,
        )
        cov = qc.get("clinvar_sites_coverage") or {}
        self.logger.info(
            f"Alignment QC: {qc.get('mapped_rate')} mapped, duplicates {qc.get('duplication_rate')}, "
            f"P/LP sites covered ≥{cov.get('min_depth')}x: {cov.get('covered')}/{cov.get('sites')}"
        )
        write_artifact(pid, "alignment_qc.json", qc)
        return qc
