"""PredictionAgent — clinical interpretation.

The risk level comes from deterministic rules (src.genomics.risk). BioGPT adds two verified texts,
whose failure never fails nor changes the conclusion:
- a literature commentary on the genes found (base BioGPT, src.llm.knowledge verification);
- an interpretation of the VCF statistics (BioGPT fine-tuned on the statistics, src.llm.stats_model):
  every sentence is checked against the statistics by src.llm.stats_interpretation, and any topic
  without a verified sentence is written from the deterministic reference text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config.settings import biogpt, biogpt_stats
from src.core import context as K
from src.core.agent import AgentResult, BaseAgent
from src.genomics import RiskLevel, assess_risk
from src.schemas.clinical_report import LEGAL_DISCLAIMER


class PredictionAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("Prediction", config)

    def _commentary(self, genes: List[str]) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
        if not genes or not biogpt().commentary:
            return None, None, None
        from src.genomics import get_panel
        from src.genomics.clinvar import ClinVarIndex, default_clinvar_path
        from src.llm.inference_engine import BioGPTCommentator
        from src.llm.knowledge import GeneDiseaseKnowledge

        clinvar = default_clinvar_path()
        knowledge = (
            GeneDiseaseKnowledge.from_clinvar_index(ClinVarIndex.load(clinvar, get_panel()))
            if clinvar.is_file() else GeneDiseaseKnowledge()
        )
        commentator = BioGPTCommentator()
        try:
            text, verification = commentator.verified_commentary(genes, knowledge)
            model = commentator.base_model + (" + LoRA" if verification.get("adapter") else "")
            self.logger.info(f"BioGPT commentary: {verification['verified']}/{verification['generated']} sentences verified")
            return (text or None), model, verification
        except Exception as e:  # optional commentary: the conclusion remains valid
            self.logger.warning(f"BioGPT commentary unavailable: {e}")
            return None, None, None
        finally:
            commentator.unload()

    def _statistics_interpretation(
        self, stats: Optional[Dict[str, Any]], risk_level: str, analysis: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not stats:
            return None
        from src.genomics import get_panel
        from src.llm.stats_interpretation import assemble_final, extract_facts

        facts = extract_facts(
            stats, risk_level,
            sorted({f["gene"] for f in analysis.get("confirmed", [])}),
            sorted({f["gene"] for f in analysis.get("to_confirm", [])}),
        )
        genes = sorted(get_panel().genes)
        note = None
        if biogpt_stats().enabled:
            from src.llm.stats_model import StatsInterpreter

            interpreter = StatsInterpreter()
            if interpreter.available:
                try:
                    result = interpreter.interpret(facts, genes)
                    m = result["metrics"]
                    self.logger.info(
                        f"Statistics interpretation: {m['verified_sentences']}/{m['generated_sentences']} sentences verified, "
                        f"{m['topics_from_model']}/{m['required_topics']} topics from the model"
                    )
                    return {**result, "source": "biogpt-stats", "facts": facts}
                except Exception as e:  # optional: the deterministic reference text takes over
                    self.logger.warning(f"BioGPT statistics interpretation unavailable: {e}")
                    note = f"model error: {e}"
                finally:
                    interpreter.unload()
            else:
                note = "no promoted statistics adapter (python -m src.llm.stats_finetune all)"
        else:
            note = "disabled (BIOGPT_STATS_INTERPRETATION=false)"
        result = assemble_final(None, facts, genes)
        return {**result, "source": "reference", "note": note, "model": None, "adapter": None, "facts": facts}

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        analysis = context[K.PANEL_ANALYSIS]
        coverage = (context.get(K.ALIGNMENT_QC) or {}).get("clinvar_sites_coverage")
        risk = assess_risk(analysis, coverage)
        genes = sorted({f["gene"] for f in analysis.get("confirmed", []) + analysis.get("to_confirm", [])})
        commentary, model, verification = self._commentary(genes)
        interpretation = self._statistics_interpretation(context.get(K.VCF_STATISTICS), risk.level.value, analysis)

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
            "commentary_verification": verification,
            "statistics_interpretation": interpretation,
            "status": "AWAITING_MEDICAL_VALIDATION",
            "legal_disclaimer": LEGAL_DISCLAIMER,
        }
        self.logger.info(f"Risk {risk.level.value} ({risk.method})")
        return AgentResult.ok(**{K.RISK_ASSESSMENT: risk.to_dict(), K.PREDICTION_RESULTS: prediction})
