"""Configuration GATK Best Practices (MarkDuplicates, BQSR)."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

from config.deployment import is_local, local_data_root

load_dotenv()


@dataclass
class GATKConfig:
    """Paramètres GATK pour prétraitement BAM."""

    docker_image: str = "broadinstitute/gatk:4.2.6.1"
    enable_mark_duplicates: bool = True
    enable_bqsr: bool = True
    known_sites_s3: str = ""
    mills_indels_s3: str = ""

    @classmethod
    def from_env(cls) -> "GATKConfig":
        common = dict(
            docker_image=os.getenv("GATK_DOCKER_IMAGE", "broadinstitute/gatk:4.2.6.1"),
            enable_mark_duplicates=os.getenv("GATK_MARK_DUPLICATES", "true").lower() == "true",
            enable_bqsr=os.getenv("GATK_BQSR", "true").lower() == "true",
        )
        if is_local():
            # Fichiers produits par scripts/download_reference.sh
            ref_dir = local_data_root() / "reference" / "hg38"
            return cls(
                **common,
                known_sites_s3=os.getenv(
                    "KNOWN_SITES_VCF",
                    str(ref_dir / "Homo_sapiens_assembly38.known_indels.vcf.gz"),
                ),
                mills_indels_s3=os.getenv(
                    "MILLS_INDELS_VCF",
                    str(ref_dir / "Mills_and_1000G_gold_standard.indels.hg38.vcf.gz"),
                ),
            )
        ref_bucket = os.getenv("S3_REFERENCE_BUCKET", "genomic-references-dev-857281493967")
        return cls(
            **common,
            known_sites_s3=os.getenv(
                "KNOWN_SITES_VCF_S3",
                f"s3://{ref_bucket}/hg38/known_sites.vcf.gz",
            ),
            mills_indels_s3=os.getenv(
                "MILLS_INDELS_VCF_S3",
                f"s3://{ref_bucket}/hg38/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz",
            ),
        )


gatk_config = GATKConfig.from_env()
