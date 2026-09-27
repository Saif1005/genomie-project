"""Statistics interpretation: facts, reference text, deterministic verifier and final answer."""

import random

import pytest

from src.genomics import get_panel
from src.llm.stats_finetune import sample_facts
from src.llm.stats_interpretation import (
    assemble_final,
    extract_facts,
    finalize_facts,
    reference_text,
    render_prompt,
    required_topics,
    split_sentences,
    verify_sentence,
)

GENES = sorted(get_panel().genes)
GERMLINE = sorted(g.symbol for g in get_panel().genes.values() if g.is_germline_breast)
HIGH = sorted(g.symbol for g in get_panel().genes.values() if g.is_germline_breast and g.penetrance == "high")


def _facts(**over):
    base = {
        "mode": "FASTQ", "variants": 1273, "snv": 960, "indel": 313, "snv_pass": 937, "n_het": 865,
        "pass_rate": 0.981, "ti_tv": 2.042, "het_hom": 2.12, "het_vaf_median": 0.5, "median_depth": 30.0,
        "min_depth": 15, "mapped_rate": 0.9991, "duplication_rate": 0.072, "site_coverage": 0.9973,
        "genes_not_excluded": ["CHEK2"], "confirmed": ["CHEK2"], "to_confirm": ["TP53"], "low_vaf_calls": 11,
        "risk_level": "MODERATE",
    }
    base.update(over)
    return finalize_facts(base)


def _ok(sentence, facts=None):
    return verify_sentence(sentence, facts or _facts(), GENES)


def test_statuses_follow_the_pipeline_thresholds():
    f = _facts(ti_tv=1.5, het_hom=3.5, duplication_rate=0.4, site_coverage=0.85, median_depth=12.0)
    assert f["status"] == {
        "pass_rate": "OK", "ti_tv": "LOW", "het_hom": "HIGH", "het_vaf": "OK", "median_depth": "LOW",
        "mapped_rate": "OK", "duplication_rate": "HIGH", "site_coverage": "LOW",
    }
    assert _facts(snv_pass=10)["status"]["ti_tv"] == "NA"


def test_extract_facts_from_statistics_json():
    stats = {
        "panel": {"variants_in_panel": 40, "by_type": {"SNV": 30, "Deletion": 6, "Insertion": 4},
                  "transitions_pass_snv": 20, "transversions_pass_snv": 10, "pass_rate": 0.95,
                  "ti_tv": 2.0, "het_hom_ratio": 1.5, "qc_flags": {"LOW_VAF_MOSAIC_OR_CHIP": 2}},
        "distributions": {"vaf_heterozygous": {"summary": {"n": 20, "median": 0.48}}, "depth": {"summary": {"median": 33}}},
        "alignment": None,
    }
    f = extract_facts(stats, "LOW")
    assert (f["mode"], f["variants"], f["snv"], f["indel"], f["low_vaf_calls"]) == ("VCF", 40, 30, 10, 2)
    assert f["status"]["site_coverage"] == "NA" and "mapped reads" not in render_prompt(f)


def test_reference_text_always_verifies_and_covers_every_topic():
    rng = random.Random(0)
    for _ in range(300):
        facts = sample_facts(rng, GERMLINE, HIGH)
        res = assemble_final(reference_text(facts, random.Random(rng.random())), facts, GENES)
        assert res["final_verified"]
        assert res["metrics"]["rejected_sentences"] == 0
        assert res["metrics"]["topics_from_model"] == len(required_topics(facts))


def test_decimal_points_do_not_split_sentences():
    assert split_sentences("Ti/Tv is 2.04, within the expected range. Risk: LOW.") == [
        "Ti/Tv is 2.04, within the expected range.", "Risk: LOW."]


@pytest.mark.parametrize("sentence", [
    "The Ti/Tv ratio of 2.04 is within the expected range (1.8–3.3).",
    "98.1% of the calls passed the GATK filters, which is within the expected range (≥ 80%).",
    "The median depth at variant sites is 30x, above the clinical threshold of 15x.",
    "The duplicate rate is 7.2%, within the expected range (≤ 30%).",
    "A pathogenic or likely pathogenic variant was confirmed in CHEK2.",
    "A variant in TP53 requires orthogonal confirmation before clinical use.",
    "A known pathogenic variant cannot be excluded in CHEK2 because of incomplete coverage.",
    "11 calls have a low allele fraction (possible mosaicism or clonal haematopoiesis) and are flagged for review.",
    "Under the deterministic rules, the risk level is MODERATE.",
    "Ti/Tv is 2.0, within the expected range.",  # coarser rounding of the same value
])
def test_true_statements_are_accepted(sentence):
    v = _ok(sentence)
    assert v.verified, v.reasons


@pytest.mark.parametrize("sentence,reason", [
    ("The Ti/Tv ratio of 2.40 is within the expected range (1.8–3.3).", "ti_tv"),                    # wrong value
    ("97.1% of the calls passed the GATK filters.", "pass_rate"),                                     # wrong percentage
    ("The Ti/Tv ratio of 2.04 is below the expected range (1.8–3.3).", "status"),                     # wrong status
    ("The duplicate rate of 7.2% is above the recommended maximum of 30%.", "status"),                # wrong direction
    ("Under the deterministic rules, the risk level is HIGH.", "risk"),                               # wrong risk
    ("The patient has a low risk of hereditary breast cancer.", "risk"),
    ("A pathogenic or likely pathogenic variant was confirmed in BRCA1.", "not in the analysis"),     # invented gene
    ("A pathogenic or likely pathogenic variant was confirmed in TP53.", "not in the analysis"),      # wrong role
    ("No pathogenic or likely pathogenic variant was confirmed in the germline panel genes.", "confirmed"),
    ("CHEK2 carriers should undergo annual breast MRI screening.", "clinical advice"),                 # advice
    ("PARP inhibitors are an option for this patient.", "clinical advice"),
    ("The Ti/Tv ratio is not within the expected range.", "negation"),
    ("The sequencing run was of excellent overall quality.", "no verifiable claim"),                   # filler
    ("There were 42 of them.", "not attached"),                                                        # orphan number
    ("The Ti/Tv ratio of 2.04 is within the expected range and", "incomplete"),                        # truncated
    ("The Ti/Tv ratio of 2.04 is within the expected range but should be reviewed, below the expected range.", "contradictory"),
])
def test_false_or_unverifiable_statements_are_rejected(sentence, reason):
    v = _ok(sentence)
    assert not v.verified
    assert any(reason in r for r in v.reasons), v.reasons


def test_final_answer_uses_verified_model_sentences_and_falls_back_elsewhere():
    facts = _facts()
    model_text = (
        "The Ti/Tv ratio of 2.04 is within the expected range (1.8–3.3). "   # verified → used
        "Under the deterministic rules, the risk level is HIGH. "            # wrong → rejected
        "Patients should consider risk-reducing surgery."                     # advice → rejected
    )
    res = assemble_final(model_text, facts, GENES)
    sources = {p["topic"]: p["source"] for p in res["per_topic"]}
    assert sources["ti_tv"] == "model"
    assert sources["risk"] == "reference" and "MODERATE" in res["text"] and "HIGH" not in res["text"]
    assert "surgery" not in res["text"]
    assert res["final_verified"] and res["metrics"]["rejected_sentences"] == 2


def test_garbage_output_gives_the_reference_text():
    facts = _facts()
    res = assemble_final("BRCA1 BRCA1 the the 2 3 4 cancer cancer", facts, GENES)
    assert res["text"] == reference_text(facts)
    assert res["metrics"]["topics_from_model"] == 0 and res["final_verified"]


def test_biogpt_detokenizer_spacing_is_normalized_before_verification():
    raw = "The Ti / Tv ratio of 2.04 is within the expected range (1.8 to 3.3). The het / hom ratio (2.12) is within the expected range."
    res = assemble_final(raw, _facts(), GENES)
    assert res["metrics"]["verified_sentences"] == 2
    assert {p["topic"]: p["source"] for p in res["per_topic"]}["het_hom"] == "model"
