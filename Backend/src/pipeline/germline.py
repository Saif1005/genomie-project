"""Pipeline germinal FASTQ → VCF filtré, restreint au panel (GPU Parabricks ou CPU GATK4).

Reprise sur incident : chaque étape écrit un point de contrôle (empreinte de la commande +
tailles des sorties). Relancer un job après une coupure reprend à la première étape non faite
au lieu de refaire des heures d'alignement.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from loguru import logger

from config.settings import gatk, parabricks, paths
from src.pipeline import commands as C
from src.pipeline.executor import Executor, PipelineError
from src.pipeline.reference import ReferenceBundle

BACKEND_GPU = "parabricks"
BACKEND_CPU = "gatk4-cpu"


@dataclass(frozen=True)
class Step:
    name: str
    command: str
    outputs: Sequence[str]
    timeout: int = 24 * 3600


@dataclass
class GermlineResult:
    backend: str
    bam: str
    vcf: str
    raw_vcf: str
    steps_run: List[str] = field(default_factory=list)
    steps_resumed: List[str] = field(default_factory=list)


class GermlinePipeline:
    def __init__(
        self,
        backend: str,
        executor: Executor,
        reference: ReferenceBundle,
        intervals_bed: str,
        threads: Optional[int] = None,
    ):
        if backend not in (BACKEND_GPU, BACKEND_CPU):
            raise ValueError(f"backend inconnu : {backend}")
        self.backend = backend
        self.executor = executor
        self.ref = reference
        self.bed = intervals_bed
        self.threads = threads or int(os.getenv("PIPELINE_THREADS", str(os.cpu_count() or 4)))

    # --- Construction des étapes ---------------------------------------------
    def _mounts(self, *files: str) -> List[str]:
        dirs = {str(Path(p).parent) for p in files if p}
        dirs.add(str(paths().data_root))
        return sorted(dirs)

    def _gatk(self, script: str, *files: str) -> str:
        spec = C.DockerSpec(gatk().image, self._mounts(self.ref.fasta, self.bed, *self.ref.known_sites, *files))
        return spec.run_script(script)

    def steps(self, patient_id: str, r1: str, r2: str, out_dir: Path) -> List[Step]:
        out, work = str(out_dir), str(out_dir / "work")
        bam, raw_vcf, vcf = f"{out}/aligned.bam", f"{out}/variants.raw.vcf.gz", f"{out}/variants.vcf.gz"
        ref, known = self.ref.fasta, list(self.ref.known_sites)
        post = Step(
            "postprocess",
            self._gatk(C.postprocess_script(ref, raw_vcf if self.backend == BACKEND_CPU else f"{out}/variants.raw.vcf", vcf, work), f"{out}/x", f"{work}/x"),
            (vcf,),
        )

        if self.backend == BACKEND_GPU:
            recal = f"{work}/recal.txt" if known else None
            pb_cfg = parabricks()
            pb = C.DockerSpec(
                pb_cfg.image,
                self._mounts(ref, self.bed, r1, r2, *known, f"{out}/x", f"{work}/x"),
                gpus=True,
                memory_gb=pb_cfg.memory_gb,
                shm_size=pb_cfg.shm_size,
                name=f"parabricks-{patient_id}",
            )
            fq2bam = C.pbrun_fq2bam(ref, r1, r2, bam, patient_id, known, recal, pb_cfg.low_memory)
            if not gatk().mark_duplicates:
                fq2bam.append("--no-markdups")
            return [
                Step("fq2bam", pb.run_args(fq2bam), (bam,) + ((recal,) if recal else ())),
                Step("haplotypecaller", pb.run_args(C.pbrun_haplotypecaller(ref, bam, f"{out}/variants.raw.vcf", self.bed, recal)), (f"{out}/variants.raw.vcf",)),
                post,
            ]

        steps = [Step("align", C.bwa_align_sort(ref, r1, r2, f"{work}/raw.bam", patient_id, self.threads), (f"{work}/raw.bam",), 48 * 3600)]
        current = f"{work}/raw.bam"
        if gatk().mark_duplicates:
            steps.append(Step("markdup", self._gatk(C.mark_duplicates(current, f"{work}/dedup.bam", f"{out}/duplicate_metrics.txt"), f"{work}/x", f"{out}/x"), (f"{work}/dedup.bam",)))
            current = f"{work}/dedup.bam"
        if known:
            steps.append(Step("bqsr", self._gatk(C.base_recalibration(ref, current, bam, f"{work}/recal.table", known), f"{work}/x", f"{out}/x"), (bam,)))
        else:
            logger.warning("Aucun site connu disponible : BQSR ignoré (qualité d'appel légèrement réduite)")
            steps.append(Step("finalize_bam", f"mv {C.q(current)} {C.q(bam)} && samtools index {C.q(bam)}", (bam,)))
        steps.append(Step("haplotypecaller", self._gatk(C.haplotype_caller(ref, bam, raw_vcf, self.bed), f"{out}/x"), (raw_vcf,)))
        steps.append(post)
        return steps

    # --- Exécution avec points de contrôle -------------------------------------
    @staticmethod
    def _marker(out_dir: Path, step: Step) -> Path:
        return out_dir / ".checkpoints" / f"{step.name}.json"

    @staticmethod
    def _fingerprint(*files: str) -> str:
        items = []
        for p in files:
            st = Path(p).stat() if p and Path(p).exists() else None
            items.append([p, st.st_size if st else None, st.st_mtime_ns if st else None])
        return hashlib.sha256(json.dumps(items).encode()).hexdigest()

    @staticmethod
    def _signature(step: Step, upstream: str) -> dict:
        """Commande + empreinte amont (chaînée) + tailles des sorties."""
        return {
            "command_sha256": hashlib.sha256(step.command.encode()).hexdigest(),
            "upstream": upstream,
            "outputs": {o: Path(o).stat().st_size for o in step.outputs if Path(o).exists()},
        }

    def _done(self, out_dir: Path, step: Step, upstream: str) -> bool:
        marker = self._marker(out_dir, step)
        if not marker.is_file() or not all(Path(o).is_file() for o in step.outputs):
            return False
        try:
            return json.loads(marker.read_text()) == self._signature(step, upstream)
        except json.JSONDecodeError:
            return False

    def run(self, patient_id: str, r1: str, r2: str, out_dir: Path) -> GermlineResult:
        (out_dir / "work").mkdir(parents=True, exist_ok=True)
        result = GermlineResult(
            backend=self.backend,
            bam=str(out_dir / "aligned.bam"),
            vcf=str(out_dir / "variants.vcf.gz"),
            raw_vcf=str(out_dir / ("variants.raw.vcf.gz" if self.backend == BACKEND_CPU else "variants.raw.vcf")),
        )
        steps = self.steps(patient_id, r1, r2, out_dir)
        # Empreinte des entrées : un FASTQ remplacé au même chemin invalide toute la chaîne
        upstream = self._fingerprint(r1, r2, self.ref.fasta, self.bed, *self.ref.known_sites)
        for i, step in enumerate(steps, 1):
            if self._done(out_dir, step, upstream):
                logger.info(f"[{self.backend}] {i}/{len(steps)} {step.name} : déjà fait (reprise)")
                result.steps_resumed.append(step.name)
                upstream = hashlib.sha256(self._marker(out_dir, step).read_bytes()).hexdigest()
                continue
            logger.info(f"[{self.backend}] {i}/{len(steps)} {step.name}…")
            res = self.executor.run(step.command, timeout=step.timeout)
            if res.returncode != 0:
                raise PipelineError(f"{step.name} a échoué (code {res.returncode}) : {res.stderr[-2000:].strip()}")
            missing = [o for o in step.outputs if not Path(o).is_file()]
            if missing:
                raise PipelineError(f"{step.name} n'a pas produit {missing}")
            marker = self._marker(out_dir, step)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(self._signature(step, upstream)))
            upstream = hashlib.sha256(marker.read_bytes()).hexdigest()
            result.steps_run.append(step.name)

        shutil.rmtree(out_dir / "work", ignore_errors=True)  # intermédiaires (BAM bruts, tables)
        return result
