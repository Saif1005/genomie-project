"""Configuration du serveur local, lue depuis l'environnement (Backend/.env).

Les réglages sont relus à chaque appel (fonctions, pas de singletons figés à l'import) :
les tests et le rechargement de .env n'ont pas besoin de redémarrer le processus.

Arborescence sous DATA_ROOT (LOCAL_DATA_ROOT) :
    reference/hg38/      FASTA, index, known-sites BQSR (GATK Resource Bundle, Broad)
    reference/clinvar/   ClinVar GRCh38 (NCBI, domaine public)
    reference/panels/    BED du panel générés automatiquement
    patients/<ID>/input  FASTQ / VCF déposés
    patients/<ID>/output BAM, VCF, artefacts JSON, rapports
    models/              cache HuggingFace, Ollama, données d'entraînement
    tmp/                 travail, cache des résultats
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Paths:
    data_root: Path

    @property
    def reference_dir(self) -> Path:
        return self.data_root / "reference"

    @property
    def hg38_dir(self) -> Path:
        return self.reference_dir / "hg38"

    @property
    def panels_dir(self) -> Path:
        return self.reference_dir / "panels"

    @property
    def clinvar_vcf(self) -> Path:
        return Path(os.getenv("CLINVAR_VCF") or self.reference_dir / "clinvar" / "clinvar_GRCh38.vcf.gz")

    @property
    def patients_dir(self) -> Path:
        return self.data_root / "patients"

    @property
    def models_dir(self) -> Path:
        return self.data_root / "models"

    @property
    def work_dir(self) -> Path:
        return self.data_root / "tmp" / "work"

    @property
    def cache_dir(self) -> Path:
        return self.data_root / "tmp" / "cache"

    @property
    def training_data(self) -> Path:
        return Path(os.getenv("TRAINING_DATA_PATH") or self.models_dir / "training" / "genomic_training_data.jsonl")


def paths() -> Paths:
    return Paths(Path(os.getenv("LOCAL_DATA_ROOT", "/data/zaynb")).resolve())


@dataclass(frozen=True)
class GATKSettings:
    image: str
    mark_duplicates: bool
    bqsr: bool
    reference_fasta: Path
    known_sites: tuple  # known_indels, Mills, dbSNP (seuls les fichiers présents sont utilisés)


def gatk() -> GATKSettings:
    hg38 = paths().hg38_dir
    return GATKSettings(
        image=os.getenv("GATK_DOCKER_IMAGE", "broadinstitute/gatk:4.2.6.1"),
        mark_duplicates=_flag("GATK_MARK_DUPLICATES", True),
        bqsr=_flag("GATK_BQSR", True),
        reference_fasta=Path(os.getenv("REFERENCE_GENOME") or hg38 / "hg38.fa"),
        known_sites=tuple(
            Path(os.getenv(env) or hg38 / default)
            for env, default in (
                ("KNOWN_SITES_VCF", "Homo_sapiens_assembly38.known_indels.vcf.gz"),
                ("MILLS_INDELS_VCF", "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz"),
                ("DBSNP_VCF", "Homo_sapiens_assembly38.dbsnp138.vcf"),
            )
        ),
    )


@dataclass(frozen=True)
class ParabricksSettings:
    image: str
    memory_gb: int
    shm_size: str
    low_memory: bool
    min_vram_gb: float


def parabricks() -> ParabricksSettings:
    return ParabricksSettings(
        image=os.getenv("PARABRICKS_IMAGE", "nvcr.io/nvidia/clara/clara-parabricks:4.6.0-1"),
        memory_gb=int(os.getenv("PARABRICKS_MEMORY_GB", "48")),
        shm_size=os.getenv("PARABRICKS_SHM_SIZE", "8g"),
        low_memory=_flag("PARABRICKS_LOW_MEMORY", False),
        min_vram_gb=float(os.getenv("PARABRICKS_MIN_VRAM_GB", "16")),
    )


@dataclass(frozen=True)
class BioGPTSettings:
    model: str
    adapter_path: Optional[str]
    device: str
    commentary: bool


def biogpt() -> BioGPTSettings:
    return BioGPTSettings(
        model=os.getenv("PREDICTION_MODEL", "microsoft/biogpt"),
        adapter_path=os.getenv("BIOGPT_ADAPTER_PATH") or None,
        device=os.getenv("LLM_DEVICE", "auto").lower(),
        commentary=_flag("BIOGPT_COMMENTARY", True) and not _flag("SKIP_BIOGPT", False),
    )


def panel_padding() -> int:
    return int(os.getenv("PANEL_INTERVAL_PADDING", "100"))


def setup_logging() -> None:
    from loguru import logger

    level = os.getenv("LOG_LEVEL", "INFO")
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )
    log_file = os.getenv("LOG_FILE_PATH")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file, level=level, rotation="100 MB", retention="30 days", compression="zip")
