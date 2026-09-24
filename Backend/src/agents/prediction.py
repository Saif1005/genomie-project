"""PredictionAgent — interprétation clinique.

Le niveau de risque vient des règles déterministes (src.genomics.risk). BioGPT n'ajoute qu'un
commentaire bibliographique, chargé seulement s'il y a un gène à commenter ; son échec ne fait
jamais échouer ni changer la conclusion.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config.settings import biogpt
from src.core import context as K
from src.core.agent import AgentResult, BaseAgent
from src.genomics import RiskLevel, assess_risk
from src.schemas.clinical_report import LEGAL_DISCLAIMER


class PredictionAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("Prediction", config)

    def _commentary(self, genes: List[str]) -> Tuple[Optional[str], Optional[str]]:
        if not genes or not biogpt().commentary:
            return None, None
        from src.llm.inference_engine import BioGPTCommentator

        commentator = BioGPTCommentator()
        try:
            text = commentator.comment_on_genes(genes)
            return (text or None), commentator.base_model
        except Exception as e:  # commentaire facultatif : la conclusion reste valide
            self.logger.warning(f"Commentaire BioGPT indisponible : {e}")
            return None, None
        finally:
            commentator.unload()

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        analysis = context[K.PANEL_ANALYSIS]
        risk = assess_risk(analysis)
        genes = sorted({f["gene"] for f in analysis.get("confirmed", []) + analysis.get("to_confirm", [])})
        commentary, model = self._commentary(genes)

        prediction = {
            "risk_level": risk.level.value,
            "diagnostic_conclusion": risk.conclusion,
            "rationale": risk.rationale,
            "limitations": risk.limitations,
            "decision_method": risk.method,
            "cancer_detected": risk.level in (RiskLevel.HIGH, RiskLevel.MODERATE),
            "cancer_types": ["breast"] if risk.level in (RiskLevel.HIGH, RiskLevel.MODERATE) else [],
            "model_commentary": commentary,
            "commentary_model": model,
            "status": "AWAITING_MEDICAL_VALIDATION",
            "legal_disclaimer": LEGAL_DISCLAIMER,
        }
        self.logger.info(f"Risque {risk.level.value} ({risk.method})")
        return AgentResult.ok(**{K.RISK_ASSESSMENT: risk.to_dict(), K.PREDICTION_RESULTS: prediction})
