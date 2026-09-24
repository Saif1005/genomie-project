"""VariantCallingAgent — FASTQ → VCF germinal filtré sur le panel (Parabricks GPU ou GATK4 CPU)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from config.settings import panel_padding, paths
from src.agents._io import patient_output_dir
from src.core import context as K
from src.core.agent import AgentError, AgentResult, BaseAgent
from src.genomics import get_panel
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
            raise AgentError(f"FASTQ inaccessible : {e}") from e

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        pid = context[K.PATIENT_ID]
        selection = select_pipeline_backend()
        backend = BACKEND_GPU if selection["backend"] == "parabricks" else BACKEND_CPU
        if backend == BACKEND_CPU:
            self.logger.warning(
                f"⚠ GATK4 sur CPU ({selection['reason']}) : plusieurs heures pour un génome, "
                "moins pour un exome ; l'appel de variants reste restreint au panel."
            )
        out_dir = patient_output_dir(pid)
        r1 = self._local_input(context[K.FASTQ_R1_URI], out_dir)
        r2 = self._local_input(context[K.FASTQ_R2_URI], out_dir)

        panel, padding = get_panel(), panel_padding()
        bed = str(panel.write_bed(paths().panels_dir / f"breast_panel_{panel.version}_pad{padding}.bed", padding))
        try:
            reference = resolve_reference(need_bwa_index=backend == BACKEND_CPU)
            result = GermlinePipeline(backend, LocalExecutor(), reference, bed).run(pid, r1, r2, out_dir)
        except PipelineError as e:
            raise AgentError(f"Appel de variants ({backend}) : {e}") from e

        self.logger.info(
            f"VCF filtré : {result.vcf} (étapes exécutées {result.steps_run}, reprises {result.steps_resumed})"
        )
        return AgentResult.ok(**{
            K.VCF_URI: result.vcf,
            K.BAM_URI: result.bam,
            K.PIPELINE_BACKEND: backend,
            "pipeline_backend_reason": selection["reason"],
            "reference": reference.fasta,
            "known_sites": list(reference.known_sites),
        })
