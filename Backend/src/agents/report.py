"""ReportGeneratorAgent — assemble le rapport clinique JSON et l'archive dans le dossier patient."""

from __future__ import annotations

from typing import Any, Dict

from src.agents._io import write_artifact
from src.core import context as K
from src.core.agent import AgentResult, BaseAgent
from src.report.clinical_report_builder import build_clinical_report


class ReportGeneratorAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("ReportGenerator", config)

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        pid = context[K.PATIENT_ID]
        report = build_clinical_report(
            context,
            execution_time_seconds=float(context.get(K.EXECUTION_TIME, 0.0)),
            steps_completed=list(context.get(K.STEPS_COMPLETED, [])) + ["report"],
            orchestration=context.get(K.ORCHESTRATION),
        )
        uri = write_artifact(pid, f"{report.report_id}.json", report.to_api_dict())
        report.report_path = uri
        self.logger.info(f"Rapport clinique : {uri}")
        return AgentResult.ok(**{K.CLINICAL_REPORT: report.to_api_dict(), K.REPORT_URI: uri})
