"""Parabricks Agent — pipeline GATK GPU déterministe (Best Practices)."""

import os
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

from src.agents.base_agent import BaseAgent, AgentResult, AgentStatus
from src.pipeline.parabricks_runner import ParabricksRunner, ParabricksRunnerError
from config.deployment import is_local
from config.gatk_config import gatk_config
from config.runtime_config import runtime_config
from src.schemas.pipeline import PipelineContext
from src.storage import StorageError, get_storage
from src.utils.gpu_manager import get_gpu_manager, GPUPhase, select_pipeline_backend


class ParabricksAgent(BaseAgent):
    """
    Séquence GATK stricte via NVIDIA Parabricks (GPU T4) :
    fq2bam (BWA-MEM) → MarkDuplicates → BQSR → HaplotypeCaller.
  """

    GATK_STEPS = ("fq2bam", "markdup", "bqsr", "haplotypecaller")

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("Parabricks", config)
        self.runner: Optional[ParabricksRunner] = None
        self.gpu = get_gpu_manager()
        self.storage = get_storage()
        self._ensure_runtime_mounts()

    def _ensure_runtime_mounts(self) -> None:
        """Crée les répertoires absolus EBS définis dans .env."""
        for mount in (
            runtime_config.data_mount,
            runtime_config.ref_mount,
            runtime_config.scratch_mount,
            runtime_config.work_mount,
        ):
            mount.mkdir(parents=True, exist_ok=True)

    def validate_input(self, context: Dict[str, Any]) -> bool:
        from src.utils.validators import validate_fastq_paths_distinct, ValidationError

        ctx = PipelineContext.from_agent_dict(context)
        if not ctx.patient_id:
            self.logger.error("patient_id requis")
            return False
        r1 = ctx.fastq_r1_s3 or ctx.fastq_r1
        r2 = ctx.fastq_r2_s3 or ctx.fastq_r2
        if not r1 or not r2:
            self.logger.error("fastq_r1_s3 et fastq_r2_s3 requis")
            return False
        try:
            validate_fastq_paths_distinct(r1, r2)
        except ValidationError as e:
            self.logger.error(str(e))
            return False
        return True

    def _patient_scratch(self, patient_id: str) -> Path:
        # Mode local : écriture directe dans patients/<ID>/output (évite de copier les BAM)
        local_out = self.storage.local_patient_dir(patient_id, "output")
        if local_out is not None:
            return local_out
        scratch = runtime_config.scratch_mount / patient_id
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def _ensure_local_file(self, uri: str, dest: Path) -> str:
        """Télécharge depuis S3 ou valide un chemin local (évite les URIs S3 dans Parabricks)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            return str(dest)
        try:
            self.logger.info(f"Préparation {uri} → {dest}")
            return self.storage.fetch(uri, dest)
        except StorageError as e:
            raise ParabricksRunnerError(str(e)) from e

    def _upload_to_s3(self, local_path: str, target_uri: str) -> str:
        """Publie une sortie dans le stockage (S3 en mode aws, no-op si déjà en place en local)."""
        if not local_path or not Path(local_path).exists():
            return target_uri
        self.logger.info(f"Publication {local_path} → {target_uri}")
        return self.storage.put(local_path, target_uri, area="output")

    def _resolve_known_sites(self, known_sites_uri: str) -> str:
        if not known_sites_uri or not gatk_config.enable_bqsr:
            return ""
        local = runtime_config.ref_mount / "hg38" / Path(known_sites_uri).name
        if local.exists() and local.stat().st_size > 0:
            return str(local)
        if not self.storage.exists(known_sites_uri):
            self.logger.warning(
                f"known_sites absent ({known_sites_uri}) — BQSR ignoré"
            )
            return ""
        return self._ensure_local_file(known_sites_uri, local)

    def _reference_uri(self) -> str:
        if is_local():
            return os.getenv(
                "REFERENCE_GENOME", str(runtime_config.ref_mount / "hg38" / "hg38.fa")
            )
        from config.aws_config import aws_config

        return aws_config.reference_genome_s3

    def _output_uri(self, patient_id: str, filename: str) -> str:
        key = self.storage.key_for(patient_id, "output", filename)
        if is_local():
            return key
        from config.aws_config import aws_config

        return f"s3://{aws_config.s3_output_bucket}/{key}"

    def _paths(self, patient_id: str) -> Dict[str, str]:
        scratch = self._patient_scratch(patient_id)
        return {
            "bam_raw": str(scratch / "aligned.raw.bam"),
            "bam_dedup": str(scratch / "aligned.dedup.bam"),
            "bam_recal": str(scratch / "aligned.recal.bam"),
            "vcf": str(scratch / "variants.vcf.gz"),
            "s3_bam_raw": self._output_uri(patient_id, "aligned.raw.bam"),
            "s3_bam_dedup": self._output_uri(patient_id, "aligned.dedup.bam"),
            "s3_bam_recal": self._output_uri(patient_id, "aligned.recal.bam"),
            "s3_vcf": self._output_uri(patient_id, "variants.vcf.gz"),
            "ref": self._resolve_reference_genome(self._reference_uri()),
            "known_sites": self._resolve_known_sites(gatk_config.known_sites_s3),
        }

    def _resolve_reference_genome(self, ref_uri: str) -> str:
        """
        Parabricks lit la référence via volume Docker local (/mnt/data/references).
        Télécharge depuis S3 si absent.
        """
        local_fa = runtime_config.ref_mount / "hg38" / "hg38.fa"
        local_fai = Path(f"{local_fa}.fai")

        if local_fa.exists() and local_fai.exists():
            self.logger.info(f"Référence locale: {local_fa}")
            return str(local_fa)

        if not ref_uri.startswith("s3://"):
            p = Path(ref_uri)
            if p.exists():
                return str(p.resolve())
            raise ParabricksRunnerError(
                f"Génome de référence introuvable: {ref_uri}. "
                "Exécutez: bash scripts/download_reference.sh"
            )

        if not self.storage.exists(ref_uri):
            raise ParabricksRunnerError(
                f"Génome de référence absent sur S3: {ref_uri}. "
                "Exécutez: bash scripts/deployment/upload_hg38_to_s3.sh"
            )

        local_fa.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.logger.info(f"Téléchargement référence {ref_uri} → {local_fa}")
            self.storage.fetch(ref_uri, local_fa)
            if self.storage.exists(f"{ref_uri}.fai"):
                self.storage.fetch(f"{ref_uri}.fai", local_fai)
            elif not local_fai.exists():
                subprocess.run(
                    ["samtools", "faidx", str(local_fa)],
                    check=True,
                    capture_output=True,
                    timeout=600,
                )
        except (StorageError, subprocess.SubprocessError) as e:
            raise ParabricksRunnerError(
                f"Impossible de préparer la référence hg38: {e}"
            ) from e

        return str(local_fa)

    def _run_gpu_pipeline(
        self,
        instance_id: str,
        ssh_key: str,
        fastq_r1: str,
        fastq_r2: str,
        paths: Dict[str, str],
    ) -> Dict[str, str]:
        self.runner = ParabricksRunner(instance_id=instance_id, ssh_key_path=ssh_key)

        with self.gpu.gpu_phase(GPUPhase.PARABRICKS, agent="parabricks"):
            # 1. fq2bam (BWA-MEM)
            self.gpu.log_transition_json(
                "parabricks", "FQ2BAM_START", s3_inputs=[fastq_r1, fastq_r2]
            )
            bam_raw = self.runner.run_fq2bam(
                fastq_r1=fastq_r1,
                fastq_r2=fastq_r2,
                output_bam=paths["bam_raw"],
                reference_genome=paths["ref"],
            )
            self.gpu.log_transition_json("parabricks", "FQ2BAM_COMPLETE", s3_output=bam_raw)

            # 2. MarkDuplicates
            bam_dedup = bam_raw
            if gatk_config.enable_mark_duplicates:
                self.gpu.log_transition_json("parabricks", "MARKDUP_START", s3_input=bam_raw)
                bam_dedup = self.runner.run_markdup(
                    bam_raw, paths["bam_dedup"], paths["ref"]
                )
                self.gpu.log_transition_json(
                    "parabricks", "MARKDUP_COMPLETE", s3_output=bam_dedup
                )

            # 3. BQSR (optionnel si known_sites disponible)
            bam_recal = bam_dedup
            if gatk_config.enable_bqsr and paths["known_sites"]:
                self.gpu.log_transition_json("parabricks", "BQSR_START", s3_input=bam_dedup)
                bam_recal = self.runner.run_bqsr(
                    input_bam=bam_dedup,
                    output_bam=paths["bam_recal"],
                    reference_genome=paths["ref"],
                    known_sites=paths["known_sites"],
                )
                self.gpu.log_transition_json(
                    "parabricks", "BQSR_COMPLETE", s3_output=bam_recal
                )

            # 4. HaplotypeCaller
            self.gpu.log_transition_json("parabricks", "HAPLOTYPECALLER_START", s3_input=bam_recal)
            vcf = self.runner.run_haplotypecaller(
                input_bam=bam_recal,
                output_vcf=paths["vcf"],
                reference_genome=paths["ref"],
            )
            self.gpu.log_transition_json(
                "parabricks", "HAPLOTYPECALLER_COMPLETE", s3_output=vcf
            )

        self.runner._destroy_parabricks_containers()
        self.gpu.release_after_parabricks()

        return {
            "bam_s3": self._upload_to_s3(bam_raw, paths["s3_bam_raw"]),
            "bam_dedup_s3": self._upload_to_s3(bam_dedup, paths["s3_bam_dedup"]),
            "bam_recal_s3": self._upload_to_s3(bam_recal, paths["s3_bam_recal"]),
            "vcf_s3": self._upload_to_s3(vcf, paths["s3_vcf"]),
            "pipeline_backend": "parabricks",
            "gatk_steps": list(self.GATK_STEPS),
            "runtime_mounts": {
                "data": str(runtime_config.data_mount),
                "ref": str(runtime_config.ref_mount),
                "scratch": str(runtime_config.scratch_mount),
                "work": str(runtime_config.work_mount),
            },
        }

    def _run_cpu_pipeline(
        self,
        fastq_r1: str,
        fastq_r2: str,
        paths: Dict[str, str],
        reason: str,
    ) -> Dict[str, str]:
        """Secours GATK4 CPU (BWA-MEM → MarkDuplicates → BQSR → HaplotypeCaller)."""
        from src.pipeline.cpu_runner import CPURunner, CPURunnerError

        self.logger.warning(
            f"⚠ Pipeline GATK4 CPU ({reason}) — durée attendue : plusieurs heures "
            "(~5 h sur 4 vCPU pour un exome, bien plus pour un génome complet)"
        )
        runner = CPURunner()
        try:
            bam_raw = runner.run_fq2bam(
                fastq_r1=fastq_r1,
                fastq_r2=fastq_r2,
                output_bam=paths["bam_raw"],
                reference_genome=paths["ref"],
            )
            bam_recal = runner.run_gatk_preprocessing(
                bam_raw, paths["bam_recal"], paths["ref"]
            )
            vcf = runner.run_haplotypecaller(
                input_bam=bam_recal,
                output_vcf=paths["vcf"],
                reference_genome=paths["ref"],
            )
        except CPURunnerError as e:
            raise ParabricksRunnerError(f"Pipeline CPU GATK4: {e}") from e
        finally:
            runner.cleanup()

        return {
            "bam_s3": self._upload_to_s3(bam_raw, paths["s3_bam_raw"]),
            "bam_recal_s3": self._upload_to_s3(bam_recal, paths["s3_bam_recal"]),
            "vcf_s3": self._upload_to_s3(vcf, paths["s3_vcf"]),
            "pipeline_backend": "gatk4-cpu",
            "pipeline_backend_reason": reason,
            "gatk_steps": list(self.GATK_STEPS),
        }

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        ctx = PipelineContext.from_agent_dict(context)
        fastq_r1 = ctx.fastq_r1_s3 or ctx.fastq_r1
        fastq_r2 = ctx.fastq_r2_s3 or ctx.fastq_r2
        if not fastq_r2 or fastq_r1 == fastq_r2:
            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error=f"fastq_r1 et fastq_r2 doivent être distincts (R1={fastq_r1})",
            )

        instance_id = (
            ctx.instance_id
            or self.config.get("instance_id")
            or os.getenv("EC2_INSTANCE_ID")
        )
        ssh_key = ctx.ssh_key or self.config.get("ssh_key") or os.getenv("SSH_KEY_PATH")

        if os.getenv("RUN_ON_EC2", "false").lower() in ("1", "true", "yes"):
            instance_id = instance_id or os.getenv("EC2_INSTANCE_ID")
            ssh_key = ssh_key or os.getenv("SSH_KEY_PATH", "/home/ubuntu/.ssh/saif_pipeline.pem")

        if not instance_id and not is_local():
            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error="EC2_INSTANCE_ID requis",
            )

        backend = select_pipeline_backend()
        self.logger.info(
            f"Moteur pipeline: {backend['backend']} ({backend['reason']})"
        )
        try:
            paths = self._paths(ctx.patient_id)
            scratch = self._patient_scratch(ctx.patient_id)
            self.logger.info(f"Pipeline GATK — répertoire de travail: {scratch}")
            local_r1 = self._ensure_local_file(
                fastq_r1, scratch / Path(fastq_r1).name
            )
            local_r2 = self._ensure_local_file(
                fastq_r2, scratch / Path(fastq_r2).name
            )
            if backend["backend"] == "cpu":
                data = self._run_cpu_pipeline(
                    local_r1, local_r2, paths, backend["reason"]
                )
            else:
                data = self._run_gpu_pipeline(
                    instance_id, ssh_key or "", local_r1, local_r2, paths
                )
            self.logger.info(f"✓ GATK complet — VCF: {data['vcf_s3']}")
            return AgentResult(
                success=True,
                status=AgentStatus.COMPLETED,
                data={**data, "patient_id": ctx.patient_id},
            )
        except ParabricksRunnerError as e:
            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error=f"Parabricks GATK pipeline failed: {e}",
            )
        finally:
            if self.runner:
                self.runner.cleanup()
