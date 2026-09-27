"""BioGPT text verification, deterministic corpus and promotion criterion (no GPU or network)."""

import pytest

from src.llm.corpus import FACT_TEMPLATES, gene_centric_genes, reference_statements, split_of
from src.llm.knowledge import (
    CORE_ASSOCIATIONS,
    CURATED_ASSOCIATIONS,
    PANEL_GENES,
    GeneDiseaseKnowledge,
    clinvar_associations,
    fallback_sentence,
    find_diseases,
    verify_text,
)
from src.llm.model_evaluator import EVAL_PROMPTS, acceptance, factual_evaluation, first_sentence


@pytest.fixture
def knowledge():
    return GeneDiseaseKnowledge()


@pytest.mark.parametrize("text,expected", [
    ("high risk of breast and ovarian cancer", ["breast_cancer", "ovarian_cancer"]),
    ("breast cancer, ovarian and endometrial cancers", ["breast_cancer", "endometrial_cancer", "ovarian_cancer"]),
    ("breast, pancreatic and thyroid carcinomas", ["breast_cancer", "pancreatic_cancer", "thyroid_cancer"]),
    ("breast and/or ovarian cancer", ["breast_cancer", "ovarian_cancer"]),
    ("breast cancer and ovarian carcinoma", ["breast_cancer", "ovarian_cancer"]),
    ("Fanconi anaemia complementation group D1", ["fanconi_anemia"]),
    ("Li-Fraumeni syndrome with osteosarcoma", ["li_fraumeni", "sarcoma"]),
    ("hereditary diffuse gastric cancer and lobular breast carcinoma", ["breast_cancer", "gastric_cancer", "lobular_breast_cancer"]),
])
def test_lexicon(text, expected):
    assert find_diseases(text) == expected


def test_correct_association_accepted(knowledge):
    v = verify_text("BRCA2", "Germline pathogenic variants in BRCA2 are associated with breast cancer and prostate cancer.", knowledge)
    assert v.verified and v.reason is None


@pytest.mark.parametrize("gene,text,reason", [
    ("RAD51D", "Carriers have breast cancer, ovarian and endometrial cancers.", "unsupported association"),
    ("PALB2", "PALB2 variants cause Lynch syndrome.", "unsupported association"),
    ("TP53", "TP53 mutations, like BRCA2, cause breast cancer.", "mentions another panel gene"),
    ("ATM", "ATM variants are associated with an increased risk.", "no specific disease"),
    ("ATM", "", "empty text"),
    ("BRCA1", "Germline pathogenic variants in BRCA1 are associated with a higher risk of breast cancer than somatic mutations.", "unverifiable claim"),
    ("BRCA2", "BRCA2 carriers have a 69% risk of breast cancer.", "unverifiable claim"),
    ("BRCA1", "BRCA1 breast cancers respond to PARP inhibitors.", "unverifiable claim"),
    ("TP53", "TP53 variants are not associated with breast cancer.", "unverifiable claim"),
    ("PALB2", "PALB2 carriers have a 5-fold increased risk of breast cancer.", "unverifiable claim"),
])

def test_rejected_claims(knowledge, gene, text, reason):
    v = verify_text(gene, text, knowledge)
    assert not v.verified and v.reason.startswith(reason)


@pytest.mark.parametrize("gene,text", [
    ("RAD51C", "Germline pathogenic variants in RAD51C are associated with ovarian cancer."),
    ("NF1", "Germline pathogenic variants in NF1 are associated with neurofibromatosis type 1 and breast cancer."),
    ("BRCA2", "Biallelic variants in BRCA2 cause Fanconi anemia complementation group D1."),
])
def test_gene_symbols_and_type_numbers_accepted(knowledge, gene, text):
    assert verify_text(gene, text, knowledge).verified


def test_clinvar_does_not_widen_accepted_associations():
    k = GeneDiseaseKnowledge(clinvar={"RAD51D": {"gastric_cancer": 50}})
    assert "gastric_cancer" not in k.supported("RAD51D")
    assert k.provenance("RAD51D", "gastric_cancer") == ["clinvar:50"]


def test_clinvar_associations_thresholds_and_generic_labels():
    recs = [("CHEK2", "Hereditary breast ovarian cancer syndrome", 2)] * 5 + [("CHEK2", "Prostate cancer", 1)] * 5 + [("CHEK2", "Prostate cancer", 2)] * 3
    assoc = clinvar_associations(recs)
    assert assoc == {"CHEK2": {"prostate_cancer": 3}}  # generic HBOC ignored; 1-star records ignored


def test_fallback_sentence_is_deterministic(knowledge):
    for gene in PANEL_GENES:
        s = fallback_sentence(gene, knowledge)
        assert s == fallback_sentence(gene, knowledge)
        assert gene in s


def test_tables_are_consistent():
    assert set(CORE_ASSOCIATIONS) == set(CURATED_ASSOCIATIONS) == set(PANEL_GENES)
    for g, core in CORE_ASSOCIATIONS.items():
        assert set(core) <= set(CURATED_ASSOCIATIONS[g])


def test_reference_statements_all_verified(knowledge):
    for doc in reference_statements():
        if "Biallelic" in doc["text"]:
            continue
        assert verify_text(doc["gene"], doc["text"], knowledge).verified, doc["text"]


def test_held_out_prompts_absent_from_training():
    train_prefixes = {t.split("{gene}")[0].strip() for t in FACT_TEMPLATES}
    for p in EVAL_PROMPTS["held_out"]:
        assert p.split("{gene}")[0].strip() not in train_prefixes


def test_hash_split_is_stable():
    assert split_of("pmid:12345") == split_of("pmid:12345")
    counts = {"train": 0, "val": 0, "test": 0}
    for i in range(5000):
        counts[split_of(f"pmid:{i}")] += 1
    assert 0.87 < counts["train"] / 5000 < 0.93


def test_gene_centric_abstract_filter():
    r = {"genes": ["RAD51D", "BRCA1"], "title": "Cancer risks in RAD51D carriers",
         "abstract": "BRCA1 BRCA2 PALB2 ATM CHEK2 were tested; RAD51D once."}
    assert gene_centric_genes(r) == ["RAD51D"]


def test_first_sentence():
    assert first_sentence("A is B. C is D.") == "A is B."
    assert first_sentence("") == ""


def test_factual_evaluation_and_promotion(knowledge):
    good = lambda p: p + " breast cancer."  # noqa: E731
    res = factual_evaluation(good, knowledge, genes=["BRCA1", "TP53"])
    assert res["metrics"]["in_distribution"]["verified_rate"] == 1.0
    base = {"perplexity": {"perplexity": 10.0}, "factual": res}
    better = {"perplexity": {"perplexity": 8.0}, "factual": res}
    assert acceptance(base, better)["accepted"]
    bad = factual_evaluation(lambda p: p + " endometrial cancer.", knowledge, genes=["BRCA1", "TP53"])
    assert not acceptance(base, {"perplexity": {"perplexity": 8.0}, "factual": bad})["accepted"]


def test_penetrance_table_matches_the_panel():
    from src.genomics import get_panel
    from src.llm.knowledge import PENETRANCE

    panel = {g.symbol: g.penetrance for g in get_panel().genes.values() if g.is_germline_breast}
    assert PENETRANCE == panel


@pytest.mark.parametrize("gene,text,ok", [
    ("CHEK2", "Germline pathogenic variants in CHEK2 are associated with a high risk of breast cancer.", False),
    ("CHEK2", "Germline pathogenic variants in CHEK2 are associated with a moderate risk of breast cancer.", True),
    ("CHEK2", "Germline pathogenic variants in CHEK2 are associated with an increased risk of breast cancer.", True),
    ("ATM", "Germline pathogenic variants in ATM confer a highly elevated risk of breast cancer.", False),
    ("BRCA2", "Germline pathogenic variants in BRCA2 are associated with a high risk of breast cancer.", True),
    ("BRCA2", "Germline pathogenic variants in BRCA2 are associated with a moderate risk of breast cancer.", False),
    ("TP53", "Germline pathogenic variants in TP53 are associated with a low risk of breast cancer.", False),
])
def test_risk_magnitude_must_match_penetrance(gene, text, ok):
    v = verify_text(gene, text, GeneDiseaseKnowledge())
    assert v.verified is ok, v.reason
