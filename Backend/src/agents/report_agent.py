"""Report Generator Agent — rapport clinique JSON structuré."""

from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
import json
from loguru import logger

from src.agents.base_agent import BaseAgent, AgentResult, AgentStatus
from src.storage import get_storage
from config.reporting_config import reporting_config
from src.report.clinical_report_builder import build_clinical_report


class ReportGeneratorAgent(BaseAgent):
    """Génère le rapport clinique JSON pour l'API et le stockage (S3 ou local)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("ReportGenerator", config)
        self.storage = get_storage()

    def validate_input(self, context: Dict[str, Any]) -> bool:
        return True

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        patient_id = context.get("patient_id")
        execution_time = float(context.get("execution_time", 0.0))
        steps = context.get("steps_completed", [])

        try:
            clinical_report = build_clinical_report(
                context,
                execution_time_seconds=execution_time,
                steps_completed=steps,
            )
            report_dict = clinical_report.to_api_dict()

            report_path = self._save_json_report(report_dict, patient_id)
            report_s3 = self.storage.put(
                report_path,
                self.storage.key_for(
                    patient_id, "report", f"{clinical_report.report_id}.json"
                ),
                area="output",
            )
            self.logger.info(f"✓ Rapport clinique JSON: {report_s3}")

            return AgentResult(
                success=True,
                status=AgentStatus.COMPLETED,
                data={
                    "report_path": report_path,
                    "report_s3": report_s3,
                    "report_format": "json",
                    "clinical_report": report_dict,
                },
            )
        except Exception as e:
            error_msg = f"Report generation failed: {e}"
            self.logger.error(error_msg)
            return AgentResult(
                success=False,
                status=AgentStatus.FAILED,
                error=error_msg,
            )

    def _save_json_report(self, data: Dict, patient_id: str) -> str:
        out_dir = Path(reporting_config.output_dir) / patient_id
        out_dir.mkdir(parents=True, exist_ok=True)
        report_id = data.get("report_id", f"REP-{patient_id}")
        report_path = out_dir / f"{report_id}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return str(report_path)
