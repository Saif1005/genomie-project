"""Validation utilities for genomic data files and inputs."""

import os
from pathlib import Path
from typing import Optional, Tuple
from loguru import logger


class ValidationError(Exception):
    """Custom exception for validation errors."""

    pass


def validate_file_exists(file_path: str, file_type: str = "file") -> None:
    """
    Validate that a file exists.

    Args:
        file_path: Path to file
        file_type: Type of file for error message

    Raises:
        ValidationError: If file doesn't exist
    """
    if not os.path.exists(file_path):
        raise ValidationError(f"{file_type.capitalize()} not found: {file_path}")
    if not os.path.isfile(file_path):
        raise ValidationError(f"Path is not a file: {file_path}")


def validate_vcf_file(vcf_path: str, require_index: bool = False) -> None:
    """
    Validate VCF file exists and optionally has index.

    Args:
        vcf_path: Path to VCF file
        require_index: Whether to require .tbi index file

    Raises:
        ValidationError: If validation fails
    """
    validate_file_exists(vcf_path, "VCF file")

    # Check if compressed VCF has index
    if vcf_path.endswith(".vcf.gz") and require_index:
        index_path = f"{vcf_path}.tbi"
        if not os.path.exists(index_path):
            raise ValidationError(
                f"VCF index file not found: {index_path}. "
                "Required for indexed VCF access."
            )

    logger.debug(f"VCF file validated: {vcf_path}")


def validate_bam_file(bam_path: str, require_index: bool = True) -> None:
    """
    Validate BAM file exists and optionally has index.

    Args:
        bam_path: Path to BAM file
        require_index: Whether to require .bai index file (default: True)

    Raises:
        ValidationError: If validation fails
    """
    validate_file_exists(bam_path, "BAM file")

    if require_index:
        bai_path = f"{bam_path}.bai"
        if not os.path.exists(bai_path):
            # Try alternative .bam.bai extension
            bai_path_alt = f"{bam_path}.bai"
            if not os.path.exists(bai_path_alt):
                raise ValidationError(
                    f"BAM index file not found: {bai_path}. "
                    "BAM files require index for random access."
                )

    logger.debug(f"BAM file validated: {bam_path}")


def validate_fastq_files(
    fastq_r1: str, fastq_r2: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Validate FASTQ files exist and are properly paired.

    Args:
        fastq_r1: Path to R1 FASTQ file
        fastq_r2: Optional path to R2 FASTQ file (for paired-end)

    Returns:
        Tuple of validated file paths

    Raises:
        ValidationError: If validation fails
    """
    validate_file_exists(fastq_r1, "FASTQ R1 file")

    # Check file extensions
    if not (fastq_r1.endswith(".fastq") or fastq_r1.endswith(".fastq.gz") or fastq_r1.endswith(".fq") or fastq_r1.endswith(".fq.gz")):
        logger.warning(
            f"FASTQ file doesn't have standard extension: {fastq_r1}"
        )

    if fastq_r2:
        validate_file_exists(fastq_r2, "FASTQ R2 file")

        # Check that R1 and R2 have matching extensions
        r1_ext = Path(fastq_r1).suffix
        r2_ext = Path(fastq_r2).suffix
        if r1_ext != r2_ext:
            logger.warning(
                f"FASTQ R1 and R2 have different extensions: "
                f"{r1_ext} vs {r2_ext}"
            )

        logger.debug(f"Paired FASTQ files validated: {fastq_r1}, {fastq_r2}")
    else:
        logger.debug(f"Single-end FASTQ file validated: {fastq_r1}")

    return fastq_r1, fastq_r2


def _normalize_fastq_path(path: str) -> str:
    return path.strip().rstrip("/")


def resolve_fastq_r2_path(fastq_r1: str, fastq_r2: Optional[str] = None) -> str:
    """
    Resolves the R2 path: explicit argument or R1→R2 derivation (R1.fastq → R2.fastq).
    """
    r1 = _normalize_fastq_path(fastq_r1)
    if fastq_r2:
        return _normalize_fastq_path(fastq_r2)
    for old, new in (
        ("/R1.fastq", "/R2.fastq"),
        ("/R1.fq", "/R2.fq"),
        ("_R1.", "_R2."),
        ("_1.fastq", "_2.fastq"),
        ("_1.fq", "_2.fq"),
        (".1.fastq", ".2.fastq"),
    ):
        if old in r1:
            return r1.replace(old, new, 1)
    raise ValidationError(
        f"fastq_r2 missing and cannot be derived from R1 ({r1}). "
        "Provide --fastq-r2 explicitly."
    )


def validate_fastq_paths_distinct(fastq_r1: str, fastq_r2: str) -> Tuple[str, str]:
    """
    Validates paired-end input: R1 and R2 required and distinct paths.

    Raises:
        ValidationError: if R2 is missing or R1 == R2
    """
    r1 = _normalize_fastq_path(fastq_r1)
    r2 = _normalize_fastq_path(fastq_r2)
    if not r1:
        raise ValidationError("fastq_r1 required")
    if not r2:
        raise ValidationError("fastq_r2 required (paired-end)")
    if r1 == r2:
        raise ValidationError(
            f"fastq_r1 and fastq_r2 point to the same file: {r1}"
        )
    logger.debug(f"Distinct FASTQ paths: R1={r1} R2={r2}")
    return r1, r2


def validate_patient_id(patient_id: str) -> None:
    """
    Validate patient ID format.

    Args:
        patient_id: Patient identifier

    Raises:
        ValidationError: If patient ID is invalid
    """
    if not patient_id:
        raise ValidationError("Patient ID cannot be empty")

    if len(patient_id) < 3:
        raise ValidationError("Patient ID must be at least 3 characters")

    if len(patient_id) > 50:
        raise ValidationError("Patient ID must be less than 50 characters")

    # Allow alphanumeric, hyphens, underscores
    if not all(c.isalnum() or c in ("-", "_") for c in patient_id):
        raise ValidationError(
            "Patient ID can only contain alphanumeric characters, "
            "hyphens, and underscores"
        )

    logger.debug(f"Patient ID validated: {patient_id}")


def validate_reference_genome(ref_path: str) -> None:
    """
    Validate reference genome FASTA file exists and has index.

    Args:
        ref_path: Path to reference genome FASTA

    Raises:
        ValidationError: If validation fails
    """
    validate_file_exists(ref_path, "Reference genome")

    # Check for .fai index
    fai_path = f"{ref_path}.fai"
    if not os.path.exists(fai_path):
        logger.warning(
            f"Reference genome index not found: {fai_path}. "
            "Indexing may be required for some tools."
        )

    logger.debug(f"Reference genome validated: {ref_path}")

