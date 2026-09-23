"""Data Manager Agent - Handles data upload, validation, and storage."""

from typing import Dict, Any, Optional
from pathlib import Path
from loguru import logger

from src.agents.base_agent import BaseAgent, AgentResult, AgentStatus
from src.storage import get_storage
from src.utils.validators import validate_fastq_files, ValidationError


class DataManagerAgent(BaseAgent):
    """Agent for managing data upload and validation."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize Data Manager Agent."""
        super().__init__("DataManager", config)
        self.storage = get_storage()

    def validate_input(self, context: Dict[str, Any]) -> bool:
        """Validate input context."""
        # Check for FASTQ files (can be local paths or S3 URIs)
        has_r1 = any(key in context for key in ["fastq_r1", "fastq_r1_path", "fastq_r1_s3"])
        has_r2 = any(key in context for key in ["fastq_r2", "fastq_r2_path", "fastq_r2_s3"])
        
        if not has_r1:
            self.logger.error("Missing FASTQ R1 file")
            return False
        
        return True

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        """
        Execute data management: validate and upload FASTQ files.

        Args:
            context: Context with patient_id and FASTQ files

        Returns:
            AgentResult with S3 paths
        """
        patient_id = context.get("patient_id")
        
        try:
            # Get FASTQ paths
            fastq_r1 = context.get("fastq_r1") or context.get("fastq_r1_path") or context.get("fastq_r1_s3")
            fastq_r2 = context.get("fastq_r2") or context.get("fastq_r2_path") or context.get("fastq_r2_s3")
            
            # Validate local files if they exist
            if fastq_r1 and not fastq_r1.startswith("s3://"):
                try:
                    validate_fastq_files(fastq_r1, fastq_r2)
                    self.logger.info(f"FASTQ files validated: {fastq_r1}")
                except ValidationError as e:
                    return AgentResult(
                        success=False,
                        status=AgentStatus.FAILED,
                        error=f"FASTQ validation failed: {e}"
                    )
            
            # Copie dans le stockage (S3 en mode aws, LOCAL_DATA_ROOT en mode local)
            fastq_r1_s3 = fastq_r1
            fastq_r2_s3 = fastq_r2

            if fastq_r1 and not self.storage.is_managed(fastq_r1):
                fastq_r1_s3 = self.storage.put(
                    fastq_r1,
                    self.storage.key_for(patient_id, "input", Path(fastq_r1).name),
                    area="input",
                )
                self.logger.info(f"Stored R1 at {fastq_r1_s3}")

            if fastq_r2 and not self.storage.is_managed(fastq_r2):
                fastq_r2_s3 = self.storage.put(
                    fastq_r2,
                    self.storage.key_for(patient_id, "input", Path(fastq_r2).name),
                    area="input",
                )
                self.logger.info(f"Stored R2 at {fastq_r2_s3}")

            return AgentResult(
                success=True,
                status=AgentStatus.COMPLETED,
                data={
                    "fastq_r1_s3": fastq_r1_s3,
                    "fastq_r2_s3": fastq_r2_s3,
                    "patient_id": patient_id,
                }
            )

        except Exception as e:
            error_msg = f"Data Manager failed: {e}"
            self.logger.error(error_msg)
            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error=error_msg
            )




