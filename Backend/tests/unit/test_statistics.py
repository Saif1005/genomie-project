"""VCF statistics and alignment QC: correctness of the computations and determinism."""

import json
import shutil

import numpy as np
import pytest

from src.genomics import ClinVarIndex, QCThresholds, Variant, analyze_panel, get_panel, iter_variants
from src.genomics.analysis import CATEGORIES
from src.genomics.statistics import (
    describe,
    histogram,
    panel_statistics,
    quantile,
    summarize_file,
    vaf_histogram,
)
from src.pipeline.alignment_qc import (
    parse_depth,
    parse_duplicate_metrics,
    parse_flagstat,
    pathogenic_sites,
    site_coverage,
)

from tests.conftest import FIXTURES


def _v(chrom, pos, ref, alt, gt="0/1", ad="10,10", qual=100.0, flt="PASS", gq=99):
    dp = sum(int(x) for x in ad.split(","))
    return Variant(chrom, pos, ref, alt, quality=qual, filter_status=flt,
                   format_data={"GT": gt, "AD": ad, "DP": str(dp), "GQ": str(gq)})


@pytest.fixture
def panel():
    return get_panel()


@pytest.fixture
def clinvar(tmp_path, panel):
    path = tmp_path / "clinvar.vcf"
    shutil.copy(FIXTURES / "clinvar_mini.vcf", path)
    return ClinVarIndex.load(path, panel)


# --- Primitives -------------------------------------------------------------------
@pytest.mark.parametrize("values", [[5.0], [1.0, 2.0], [3.0, 1.0, 2.0, 10.0], list(range(1, 102))])
def test_quantiles_match_numpy(values):
    xs = sorted(float(v) for v in values)
    for q in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert quantile(xs, q) == pytest.approx(float(np.quantile(xs, q)))


def test_describe_mean_and_sample_sd():
    d = describe([2, 4, 4, 4, 5, 5, 7, 9, None])
    assert d["n"] == 8
    assert d["mean"] == 5.0
    assert d["sd"] == pytest.approx(float(np.std([2, 4, 4, 4, 5, 5, 7, 9], ddof=1)), abs=1e-4)
    assert describe([])["median"] is None


def test_histogram_fixed_edges_and_open_bin():
    h = histogram([0, 9.9, 10, 25, 1000, None], (0, 10, 20))
    assert [b["count"] for b in h] == [2, 1, 2]
    assert h[-1]["hi"] is None


def test_vaf_histogram_one_in_last_bin():
    h = vaf_histogram([0.0, 0.5, 0.999, 1.0])
    assert sum(b["count"] for b in h) == 4
    assert h[10]["count"] == 1 and h[19]["count"] == 2


# --- File summary -------------------------------------------------------------------
def test_titv_hethom_and_filters_computed_by_hand():
    vs = [
        _v("chr1", 10, "A", "G"),             # transition
        _v("chr1", 20, "C", "T", gt="1/1", ad="0,20"),  # transition, homozygote
        _v("chr1", 30, "A", "C"),             # transversion
        _v("chr1", 40, "G", "T", flt="LowQual"),  # filtered: excluded from Ti/Tv
        _v("chr2", 50, "AT", "A"),            # deletion
        _v("chr2", 60, "A", "G", gt="0/0", ad="20,0"),  # not carried
    ]
    s = summarize_file(vs)
    assert s["records_read"] == 6 and s["alleles_called"] == 5
    assert (s["transitions_pass_snv"], s["transversions_pass_snv"], s["ti_tv"]) == (2, 1, 2.0)
    assert s["by_zygosity"] == {"heterozygous": 4, "homozygous": 1}
    assert s["het_hom_ratio"] == 4.0
    assert s["by_filter"] == {"PASS": 4, "LowQual": 1}
    assert s["pass_rate"] == 0.8
    assert s["by_type"] == {"SNV": 4, "Deletion": 1}


# --- Panel statistics ---------------------------------------------------------------
def _stats(panel, clinvar, fixture):
    variants = list(iter_variants(FIXTURES / fixture))
    analysis = analyze_panel(variants, panel, clinvar, QCThresholds())
    return analysis, panel_statistics(analysis, summarize_file(variants))


def test_every_panel_variant_is_categorised(panel, clinvar):
    analysis, stats = _stats(panel, clinvar, "patient_brca1_high.vcf")
    rows = stats["variants"]
    assert len(rows) == analysis.variants_in_panel == stats["panel"]["variants_in_panel"]
    assert all(r["category"] in CATEGORIES for r in rows)
    assert sum(stats["panel"]["by_category"].values()) == len(rows)
    assert stats["panel"]["by_category"]["pathogenic_confirmed"] == len(analysis.confirmed)
    # the full table contains the reported (P/LP) variants and the others too
    assert {(r["chromosome"], r["position"]) for r in rows} >= {
        (f.variant.chromosome, f.variant.position) for f in analysis.confirmed
    }


def test_per_gene_table_covers_the_whole_panel(panel, clinvar):
    analysis, stats = _stats(panel, clinvar, "patient_brca1_high.vcf")
    genes = [g["gene"] for g in stats["per_gene"]]
    assert genes == analysis.panel_genes
    assert sum(g["variants"] for g in stats["per_gene"]) == len(stats["variants"])


def test_checks_na_with_too_few_variants(panel, clinvar):
    _, stats = _stats(panel, clinvar, "patient_brca1_high.vcf")
    checks = {c["id"]: c for c in stats["quality_checks"]}
    assert checks["ti_tv"]["status"] == "NA"        # < 30 SNVs: ratio not interpretable
    assert checks["pass_rate"]["status"] in ("OK", "WARN")
    assert "mapped_rate" not in checks              # no BAM in VCF mode


@pytest.mark.parametrize("fixture", ["patient_brca1_high.vcf", "patient_negative.vcf", "patient_chek2_multiallelic.vcf"])
def test_statistics_are_deterministic(panel, clinvar, fixture):
    _, a = _stats(panel, clinvar, fixture)
    _, b = _stats(panel, clinvar, fixture)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_statistics_are_json_serialisable(panel, clinvar):
    _, stats = _stats(panel, clinvar, "patient_brca2_lowvaf.vcf")
    json.dumps(stats, allow_nan=False)


# --- Alignment QC --------------------------------------------------------------------
FLAGSTAT = """1000 + 0 in total (QC-passed reads + QC-failed reads)
990 + 0 primary
0 + 0 secondary
10 + 0 supplementary
50 + 0 duplicates
50 + 0 primary duplicates
980 + 0 mapped (98.00% : N/A)
970 + 0 primary mapped (97.98% : N/A)
990 + 0 paired in sequencing
495 + 0 read1
495 + 0 read2
960 + 0 properly paired (96.97% : N/A)
"""

DUP = """## htsjdk.samtools.metrics.StringHeader
## METRICS CLASS\tpicard.sam.DuplicationMetrics
LIBRARY\tUNPAIRED_READS_EXAMINED\tREAD_PAIRS_EXAMINED\tSECONDARY_OR_SUPPLEMENTARY_RDS\tUNMAPPED_READS\tUNPAIRED_READ_DUPLICATES\tREAD_PAIR_DUPLICATES\tREAD_PAIR_OPTICAL_DUPLICATES\tPERCENT_DUPLICATION\tESTIMATED_LIBRARY_SIZE
P1\t3911\t850377\t822\t4335\t53\t2182\t0\t0.002588\t164271337

## HISTOGRAM\tjava.lang.Double
"""


def test_parse_flagstat_uses_mapped_not_primary_mapped():
    f = parse_flagstat(FLAGSTAT)
    assert f["total_reads"] == 1000 and f["mapped_reads"] == 980
    assert f["mapped_rate"] == 0.98
    assert f["properly_paired_rate"] == round(960 / 990, 4)


def test_parse_duplicate_metrics():
    d = parse_duplicate_metrics(DUP)
    assert d["duplication_rate"] == 0.0026
    assert d["read_pairs_examined"] == 850377
    assert parse_duplicate_metrics("vide")["duplication_rate"] is None


def test_indel_site_coverage_is_minimum_over_its_bases():
    sites = [("BRCA1", "chr17", 100, 100, "a"), ("BRCA1", "chr17", 200, 202, "b"), ("TP53", "chr17", 300, 300, "c")]
    depth = parse_depth("chr17\t100\t30\nchr17\t200\t40\nchr17\t201\t8\nchr17\t202\t40\nchr17\t300\t0\n")
    cov = site_coverage(sites, depth, 15, ["BRCA1", "TP53", "PALB2"])
    per = {g["gene"]: g for g in cov["per_gene"]}
    assert per["BRCA1"]["covered"] == 1 and per["BRCA1"]["sites"] == 2   # the indel has one base at 8x
    assert per["TP53"]["zero_depth_sites"] == 1
    assert per["PALB2"]["sites"] == 0 and per["PALB2"]["fraction_covered"] is None
    assert cov["fraction_covered"] == round(1 / 3, 4)


def test_pathogenic_sites_restricted_to_germline_genes(panel, clinvar):
    sites = pathogenic_sites(clinvar, panel)
    germline = {g.symbol for g in panel.germline_breast_genes()}
    assert sites and all(s[0] in germline for s in sites)
    assert sites == sorted(sites, key=lambda s: (s[1], s[2], s[4]))
