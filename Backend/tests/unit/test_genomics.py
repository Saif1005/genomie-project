"""Domaine scientifique : lecture VCF, ClinVar, QC, classification et risque."""

import gzip
import shutil

import pytest

from src.genomics import (
    AnnotationUnavailable,
    ClinicalSignificance,
    ClinVarIndex,
    EmbeddedClinVar,
    QCThresholds,
    RiskLevel,
    analyze_panel,
    assess_risk,
    build_annotator,
    get_panel,
    iter_variants,
    parse_clnsig,
    read_header,
)
from src.genomics.clinvar import review_stars
from src.genomics.vcf_io import trim_alleles

from tests.conftest import FIXTURES


@pytest.fixture
def panel():
    return get_panel()


@pytest.fixture
def clinvar(tmp_path, panel):
    path = tmp_path / "clinvar.vcf"
    shutil.copy(FIXTURES / "clinvar_mini.vcf", path)
    return ClinVarIndex.load(path, panel)


def _analyze(name, panel, annotator):
    regions = [(c, s, e) for c, s, e, _ in panel.intervals(padding=100)]
    variants = iter_variants(FIXTURES / name, regions)
    return analyze_panel(variants, panel, annotator, QCThresholds()).to_dict()


@pytest.mark.parametrize(
    "raw, expected, low_pen",
    [
        ("Pathogenic", ClinicalSignificance.PATHOGENIC, False),
        ("Likely_pathogenic", ClinicalSignificance.LIKELY_PATHOGENIC, False),
        ("Pathogenic/Likely_pathogenic", ClinicalSignificance.PATHOGENIC, False),
        ("Pathogenic|risk_factor", ClinicalSignificance.PATHOGENIC, False),
        ("Pathogenic,_low_penetrance", ClinicalSignificance.PATHOGENIC, True),
        ("Conflicting_classifications_of_pathogenicity", ClinicalSignificance.CONFLICTING, False),
        ("Conflicting_interpretations_of_pathogenicity", ClinicalSignificance.CONFLICTING, False),
        ("Likely_benign", ClinicalSignificance.LIKELY_BENIGN, False),
        ("Benign/Likely_benign", ClinicalSignificance.BENIGN, False),
        ("Uncertain_significance", ClinicalSignificance.VUS, False),
        ("risk_factor", ClinicalSignificance.RISK_ALLELE, False),
        ("", ClinicalSignificance.OTHER, False),
    ],
)
def test_parse_clnsig(raw, expected, low_pen):
    assert parse_clnsig(raw) == (expected, low_pen)


def test_review_stars():
    assert review_stars("reviewed_by_expert_panel") == 3
    assert review_stars("criteria_provided,_single_submitter") == 1
    assert review_stars("no_assertion_criteria_provided") == 0


def test_panel_is_germline_hereditary(panel):
    germline = {g.symbol for g in panel.germline_breast_genes()}
    assert {"BRCA1", "BRCA2", "PALB2", "CHEK2", "ATM", "CDH1"} <= germline
    assert not {"PIK3CA", "ERBB2", "MYC"} & germline
    assert panel.get("HER2").symbol == "ERBB2"


def test_panel_bed_is_zero_based_and_sorted(panel, tmp_path):
    bed = panel.write_bed(tmp_path / "panel.bed", padding=0).read_text().splitlines()
    brca1 = next(line for line in bed if line.endswith("BRCA1")).split("\t")
    assert int(brca1[1]) == panel.get("BRCA1").start - 1
    chroms = [line.split("\t")[0] for line in bed]
    assert chroms.index("chr2") < chroms.index("chr10") < chroms.index("chr22")


def test_vcf_reader_splits_multiallelic_with_per_allele_metrics():
    variants = list(iter_variants(FIXTURES / "patient_chek2_multiallelic.vcf"))
    assert [(v.alt, v.alt_index) for v in variants] == [("C", 1), ("G", 2)]
    c_allele, g_allele = variants
    assert not c_allele.is_called and g_allele.is_called
    assert g_allele.zygosity == "heterozygous"
    assert g_allele.vaf == pytest.approx(19 / 39)
    assert g_allele.info["AF"] == "0.5"


def test_vcf_reader_restricts_to_regions_and_reads_gzip(tmp_path, panel):
    gz = tmp_path / "p.vcf.gz"
    with open(FIXTURES / "patient_brca1_high.vcf", "rb") as src, gzip.open(gz, "wb") as dst:
        dst.write(src.read())
    regions = [(c, s, e) for c, s, e, _ in panel.intervals()]
    positions = [v.position for v in iter_variants(gz, regions)]
    assert 1014143 not in positions  # chr1 hors panel, ignoré à la lecture
    assert 43057062 in positions


def test_vep_csq_symbol_read_from_header_format():
    v = next(iter_variants(FIXTURES / "patient_palb2_lof.vcf"))
    assert v.gene == "PALB2"
    assert v.consequence == "frameshift_variant"


def test_population_af_never_uses_gatk_sample_af():
    v = next(iter_variants(FIXTURES / "patient_brca1_high.vcf"))
    assert v.info["AF"] == "0.5"
    assert v.gnomad_af is None


def test_trim_alleles_matches_split_multiallelic_indel():
    assert trim_alleles(100, "GTT", "GT") == (100, "GT", "G")
    assert trim_alleles(100, "A", "G") == (100, "A", "G")


def test_clinvar_index_keeps_only_panel_and_is_cached(tmp_path, panel, clinvar):
    assert clinvar.version == "2026-09-01"
    assert len(clinvar) == 8  # ISG15 (chr1, hors panel) exclu
    cache_files = list(tmp_path.glob("clinvar.vcf.panel-*.json"))
    assert len(cache_files) == 1
    again = ClinVarIndex.load(tmp_path / "clinvar.vcf", panel)
    assert again._records == clinvar._records


def test_brca1_confirmed_gives_high(panel, clinvar):
    analysis = _analyze("patient_brca1_high.vcf", panel, clinvar)
    assert analysis["identified_genes"] == ["BRCA1"]
    f = analysis["confirmed"][0]
    assert (f["position"], f["mutation"], f["zygosity"]) == (43057062, "T>TG", "heterozygous")
    assert f["review_stars"] == 3 and f["rsid"] == "rs80357906"
    assert len(analysis["vus"]) == 1
    risk = assess_risk(analysis)
    assert risk.level is RiskLevel.HIGH


def test_low_vaf_pathogenic_is_indeterminate_not_low(panel, clinvar):
    analysis = _analyze("patient_brca2_lowvaf.vcf", panel, clinvar)
    assert analysis["confirmed"] == []
    assert analysis["to_confirm"][0]["gene"] == "BRCA2"
    assert "VAF_BASSE_MOSAIQUE_OU_CHIP" in analysis["to_confirm"][0]["qc_flags"]
    assert assess_risk(analysis).level is RiskLevel.INDETERMINATE


def test_moderate_gene_on_second_allele_gives_moderate(panel, clinvar):
    analysis = _analyze("patient_chek2_multiallelic.vcf", panel, clinvar)
    assert [f["mutation"] for f in analysis["confirmed"]] == ["A>G"]
    assert assess_risk(analysis).level is RiskLevel.MODERATE


def test_negative_patient_ignores_somatic_and_uncalled(panel, clinvar):
    analysis = _analyze("patient_negative.vcf", panel, clinvar)
    assert analysis["confirmed"] == [] and analysis["to_confirm"] == []
    assert analysis["somatic_genes_skipped"] == 1
    assert assess_risk(analysis).level is RiskLevel.LOW


def test_unclassified_loss_of_function_needs_review(panel, clinvar):
    analysis = _analyze("patient_palb2_lof.vcf", panel, clinvar)
    assert analysis["to_confirm"][0]["gene"] == "PALB2"
    assert assess_risk(analysis).level is RiskLevel.INDETERMINATE


def test_embedded_annotation_used_when_no_database(tmp_path, panel):
    annotator = build_annotator(True, panel, clinvar_vcf=tmp_path / "absent.vcf.gz")
    assert isinstance(annotator, EmbeddedClinVar)


def test_no_annotation_at_all_refuses_to_conclude(tmp_path, panel):
    assert not read_header(FIXTURES / "patient_brca1_high.vcf").has_clinvar_annotation
    with pytest.raises(AnnotationUnavailable):
        build_annotator(False, panel, clinvar_vcf=tmp_path / "absent.vcf.gz")


def test_analysis_is_deterministic(panel, clinvar):
    first = _analyze("patient_brca1_high.vcf", panel, clinvar)
    second = _analyze("patient_brca1_high.vcf", panel, clinvar)
    assert first == second
    assert assess_risk(first) == assess_risk(second)
