"""PredictionAgent: interpretation of the VCF statistics never changes the rule-based risk."""

from types import SimpleNamespace

import pytest

from src.agents import prediction as pred
from src.llm import stats_model

STATS = {
    "panel": {"variants_in_panel": 120, "by_type": {"SNV": 100, "Deletion": 12, "Insertion": 8},
              "transitions_pass_snv": 66, "transversions_pass_snv": 30, "pass_rate": 0.97,
              "ti_tv": 2.2, "het_hom_ratio": 1.6, "qc_flags": {}},
    "distributions": {"vaf_heterozygous": {"summary": {"n": 70, "median": 0.49}}, "depth": {"summary": {"median": 42}}},
    "alignment": None,
}
ANALYSIS = {"confirmed": [{"gene": "BRCA2", "penetrance": "high", "chromosome": "chr13", "position": 32340301, "mutation": "GT>G", "clinvar_significance": "Pathogenic", "zygosity": "heterozygous"}], "to_confirm": [], "panel_genes": ["BRCA2"], "variants_in_panel": 120}


@pytest.fixture(autouse=True)
def no_commentary(monkeypatch):
    monkeypatch.setenv("BIOGPT_COMMENTARY", "false")
    monkeypatch.setenv("SKIP_BIOGPT", "false")  # the test suite skips BioGPT globally


def _run(monkeypatch, interpreter_cls):
    monkeypatch.setattr(stats_model, "StatsInterpreter", interpreter_cls)
    return pred.PredictionAgent().execute({"patient_id": "P1", "panel_analysis": ANALYSIS, "vcf_statistics": STATS}).data


class _Unavailable:
    available = False

    def unload(self):
        pass


def test_without_promoted_adapter_the_reference_text_is_used(monkeypatch):
    out = _run(monkeypatch, _Unavailable)["prediction_results"]
    interp = out["statistics_interpretation"]
    assert out["risk_level"] == "HIGH"
    assert interp["source"] == "reference" and interp["final_verified"]
    assert "BRCA2" in interp["text"] and "HIGH" in interp["text"]


def test_model_output_is_verified_and_a_wrong_risk_never_reaches_the_report(monkeypatch):
    from src.llm.stats_interpretation import assemble_final

    class _Model:
        available = True

        def interpret(self, facts, genes):
            text = "The Ti/Tv ratio of 2.20 is within the expected range (1.8–3.3). The risk level is LOW."
            return {**assemble_final(text, facts, genes), "model_output": text, "model": "biogpt", "adapter": "/a"}

        def unload(self):
            pass

    out = _run(monkeypatch, _Model)["prediction_results"]
    interp = out["statistics_interpretation"]
    assert out["risk_level"] == "HIGH"
    assert interp["source"] == "biogpt-stats"
    topics = {p["topic"]: p["source"] for p in interp["per_topic"]}
    assert topics["ti_tv"] == "model" and topics["risk"] == "reference"
    assert "risk level is LOW" not in interp["text"] and interp["final_verified"]


def test_model_failure_falls_back_to_the_reference_text(monkeypatch):
    class _Broken:
        available = True

        def interpret(self, facts, genes):
            raise RuntimeError("CUDA out of memory")

        def unload(self):
            pass

    interp = _run(monkeypatch, _Broken)["prediction_results"]["statistics_interpretation"]
    assert interp["source"] == "reference" and "CUDA out of memory" in interp["note"]
