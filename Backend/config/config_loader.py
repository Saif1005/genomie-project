"""Centralized configuration loader for all pipeline configurations."""

from config.aws_config import aws_config
from config.deployment import is_local
from config.parabricks_config import parabricks_config
from config.llm_config import llm_config
from config.logging_config import logging_config
from config.pipeline_config import pipeline_config
from config.quality_control_config import quality_control_config
from config.database_config import database_config
from config.reporting_config import reporting_config
from loguru import logger


class ConfigLoader:
    """Centralized loader for all pipeline configurations."""

    def __init__(self):
        """Initialize configuration loader."""
        self.aws = aws_config
        self.parabricks = parabricks_config
        self.llm = llm_config
        self.logging = logging_config
        self.pipeline = pipeline_config
        self.quality_control = quality_control_config
        self.database = database_config
        self.reporting = reporting_config

    def validate(self) -> bool:
        """
        Validate all configurations.

        Returns:
            True if all configurations are valid

        Raises:
            ValueError: If any configuration is invalid
        """
        errors = []

        # Validate AWS config (mode aws uniquement)
        if not is_local():
            if not self.aws.region:
                errors.append("AWS_REGION is required")
            if not self.aws.account_id:
                errors.append("AWS_ACCOUNT_ID is required")

        # Validate Parabricks config
        if not self.parabricks.image:
            errors.append("PARABRICKS_IMAGE is required")

        # Validate LLM config
        if not self.llm.model_name:
            errors.append("LLM_MODEL_NAME is required")

        # Validate pipeline config
        if self.pipeline.max_workers < 1:
            errors.append("PIPELINE_MAX_WORKERS must be >= 1")
        if self.pipeline.timeout_hours < 1:
            errors.append("PIPELINE_TIMEOUT_HOURS must be >= 1")

        # Validate quality control config
        if self.quality_control.min_coverage < 1:
            errors.append("MIN_COVERAGE must be >= 1")
        if not 0 <= self.quality_control.min_vaf <= 1:
            errors.append("MIN_VAF must be between 0 and 1")

        # Validate reporting config
        if self.reporting.format not in ("pdf", "html", "json"):
            errors.append("REPORT_FORMAT must be pdf, html, or json")

        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(
                f"  - {error}" for error in errors
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info("All configurations validated successfully")
        return True

    def print_summary(self) -> None:
        """Print summary of all configurations."""
        logger.info("=" * 60)
        logger.info("Pipeline Configuration Summary")
        logger.info("=" * 60)

        logger.info("\nAWS Configuration:")
        logger.info(f"  Region: {self.aws.region}")
        logger.info(f"  Input Bucket: {self.aws.s3_input_bucket}")
        logger.info(f"  Output Bucket: {self.aws.s3_output_bucket}")
        logger.info(f"  EC2 Instance Type: {self.aws.ec2_instance_type}")

        logger.info("\nParabricks Configuration:")
        logger.info(f"  Image: {self.parabricks.image}")
        logger.info(f"  GPU Count: {self.parabricks.gpu_count}")

        logger.info("\nLLM Configuration:")
        logger.info(f"  Model: {self.llm.model_name}")
        logger.info(f"  Device: {self.llm.device}")

        logger.info("\nPipeline Configuration:")
        logger.info(f"  Work Directory: {self.pipeline.work_dir}")
        logger.info(f"  Max Workers: {self.pipeline.max_workers}")
        logger.info(f"  Timeout: {self.pipeline.timeout_hours} hours")

        logger.info("\nQuality Control Configuration:")
        logger.info(f"  Min Coverage: {self.quality_control.min_coverage}x")
        logger.info(f"  Min VAF: {self.quality_control.min_vaf}")
        logger.info(f"  Min Quality Score: {self.quality_control.min_quality_score}")

        logger.info("\nReporting Configuration:")
        logger.info(f"  Output Directory: {self.reporting.output_dir}")
        logger.info(f"  Format: {self.reporting.format.upper()}")
        logger.info(f"  Language: {self.reporting.language}")

        logger.info("=" * 60)


# Global instance
config_loader = ConfigLoader()

