"""Construction des commandes GATK / Parabricks (fonctions pures, testables sans exécution).

Choix bioinformatiques :
- BWA-MEM `-K 100000000` : taille de lot fixe → alignement identique quel que soit le nombre de
  threads (reproductibilité bit à bit) ; `-Y` : soft-clipping des alignements supplémentaires.
- Alignement → tri en flux (pas de SAM intermédiaire sur disque).
- BQSR avec tous les sites connus disponibles (known_indels, Mills, dbSNP).
- HaplotypeCaller restreint aux régions du panel (BED avec marge) : minutes au lieu d'heures.
- Parabricks : fq2bam fait l'alignement, le marquage des duplicats et la table BQSR en un
  passage ; haplotypecaller applique la recalibration à la volée (--in-recal-file).
- Post-traitement commun GPU/CPU : normalisation (alignement à gauche, éclatement des
  multi-alléliques) puis filtres « hard » GATK séparés SNV / indels.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

# Filtres recommandés par GATK pour un échantillon unique (VQSR impossible sur un panel)
SNP_FILTERS = (
    ("SNP_QD2", "QD < 2.0"),
    ("SNP_QUAL30", "QUAL < 30.0"),
    ("SNP_SOR3", "SOR > 3.0"),
    ("SNP_FS60", "FS > 60.0"),
    ("SNP_MQ40", "MQ < 40.0"),
    ("SNP_MQRankSum", "MQRankSum < -12.5"),
    ("SNP_ReadPosRankSum", "ReadPosRankSum < -8.0"),
)
INDEL_FILTERS = (
    ("INDEL_QD2", "QD < 2.0"),
    ("INDEL_QUAL30", "QUAL < 30.0"),
    ("INDEL_FS200", "FS > 200.0"),
    ("INDEL_ReadPosRankSum", "ReadPosRankSum < -20.0"),
)


def q(value: str) -> str:
    return shlex.quote(str(value))


@dataclass(frozen=True)
class DockerSpec:
    image: str
    mounts: Sequence[str]
    gpus: bool = False
    memory_gb: Optional[int] = None
    shm_size: Optional[str] = None
    name: Optional[str] = None

    def prefix(self) -> List[str]:
        args = ["docker", "run", "--rm"]
        if self.name:
            args += ["--name", self.name]
        if self.gpus:
            args += ["--gpus", "all"]
        if self.memory_gb:
            args.append(f"--memory={self.memory_gb}g")
        if self.shm_size:
            args.append(f"--shm-size={self.shm_size}")
        for m in sorted(set(self.mounts)):
            args += ["-v", f"{m}:{m}"]
        return args + [self.image]

    def run_script(self, script: str) -> str:
        return " ".join(q(a) for a in self.prefix()) + " bash -c " + q(f"set -euo pipefail; {script}")

    def run_args(self, args: Iterable[str]) -> str:
        return " ".join(q(a) for a in [*self.prefix(), *args])


def read_group(patient_id: str) -> str:
    return f"@RG\\tID:{patient_id}\\tSM:{patient_id}\\tLB:{patient_id}\\tPL:ILLUMINA\\tPU:{patient_id}"


# --- CPU (BWA + GATK4) ----------------------------------------------------------
def bwa_align_sort(ref: str, r1: str, r2: str, out_bam: str, patient_id: str, threads: int) -> str:
    sort_threads = max(1, threads // 4)
    return (
        f"set -o pipefail; bwa mem -t {threads} -K 100000000 -Y -R {q(read_group(patient_id))} "
        f"{q(ref)} {q(r1)} {q(r2)} "
        f"| samtools sort -@ {sort_threads} -m 1G -o {q(out_bam)} - "
        f"&& samtools index {q(out_bam)}"
    )


def mark_duplicates(in_bam: str, out_bam: str, metrics: str) -> str:
    return (
        f"gatk MarkDuplicates -I {q(in_bam)} -O {q(out_bam)} -M {q(metrics)} "
        "--CREATE_INDEX true --VALIDATION_STRINGENCY SILENT"
    )


def base_recalibration(ref: str, in_bam: str, out_bam: str, table: str, known_sites: Sequence[str]) -> str:
    sites = " ".join(f"--known-sites {q(s)}" for s in known_sites)
    return (
        f"gatk BaseRecalibrator -R {q(ref)} -I {q(in_bam)} {sites} -O {q(table)} && "
        f"gatk ApplyBQSR -R {q(ref)} -I {q(in_bam)} --bqsr-recal-file {q(table)} -O {q(out_bam)}"
    )


def haplotype_caller(ref: str, in_bam: str, out_vcf: str, intervals: str) -> str:
    return (
        f"gatk HaplotypeCaller -R {q(ref)} -I {q(in_bam)} -L {q(intervals)} "
        f"-O {q(out_vcf)} --native-pair-hmm-threads 4"
    )


# --- GPU (Parabricks) -----------------------------------------------------------
def pbrun_fq2bam(
    ref: str, r1: str, r2: str, out_bam: str, patient_id: str,
    known_sites: Sequence[str], recal_table: Optional[str], low_memory: bool,
) -> List[str]:
    args = [
        "pbrun", "fq2bam", "--ref", ref, "--in-fq", r1, r2, "--out-bam", out_bam,
        "--read-group-sm", patient_id, "--read-group-lb", patient_id,
        "--read-group-pl", "ILLUMINA", "--read-group-id-prefix", patient_id,
    ]
    if known_sites and recal_table:
        for s in known_sites:
            args += ["--knownSites", s]
        args += ["--out-recal-file", recal_table]
    if low_memory:
        args.append("--low-memory")
    return args


def pbrun_haplotypecaller(ref: str, in_bam: str, out_vcf: str, intervals: str, recal_table: Optional[str]) -> List[str]:
    args = ["pbrun", "haplotypecaller", "--ref", ref, "--in-bam", in_bam, "--out-variants", out_vcf,
            "--interval-file", intervals]
    if recal_table:
        args += ["--in-recal-file", recal_table]
    return args


# --- Post-traitement commun -------------------------------------------------------
def _filters(filters: Sequence[tuple]) -> str:
    return " ".join(f"--filter-name {q(n)} --filter-expression {q(e)}" for n, e in filters)


def postprocess_script(ref: str, raw_vcf: str, out_vcf: str, work: str) -> str:
    norm, snp, indel = f"{work}/norm.vcf.gz", f"{work}/snp.vcf.gz", f"{work}/indel.vcf.gz"
    snp_f, indel_f = f"{work}/snp.filtered.vcf.gz", f"{work}/indel.filtered.vcf.gz"
    return " && ".join([
        f"gatk IndexFeatureFile -I {q(raw_vcf)}",
        f"gatk LeftAlignAndTrimVariants -R {q(ref)} -V {q(raw_vcf)} -O {q(norm)} --split-multi-allelics",
        f"gatk SelectVariants -V {q(norm)} --select-type-to-include SNP -O {q(snp)}",
        f"gatk SelectVariants -V {q(norm)} --select-type-to-exclude SNP -O {q(indel)}",
        f"gatk VariantFiltration -V {q(snp)} -O {q(snp_f)} {_filters(SNP_FILTERS)}",
        f"gatk VariantFiltration -V {q(indel)} -O {q(indel_f)} {_filters(INDEL_FILTERS)}",
        f"gatk MergeVcfs -I {q(snp_f)} -I {q(indel_f)} -O {q(out_vcf)}",
    ])
