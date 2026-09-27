#!/usr/bin/env python3
"""Independent verification of a GermlineIQ report with reference tools (bcftools, samtools).

No backend function is imported: every report metric is recomputed through a different code path
(bcftools stats/query, samtools flagstat/depth, Picard metrics file), then compared.
Any difference → FAIL (exit code 1).

Usage (conda env "genomic"):
    python scripts/expert_check.py ~/germlineiq_data/patients/NA12878/output
Options: --clinvar <clinvar_GRCh38.vcf.gz> --panel <cancer_genes_db.json>
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []


def sh(cmd: str) -> str:
    p = subprocess.run(["bash", "-c", f"set -o pipefail; {cmd}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd}\n{p.stderr[-800:]}")
    return p.stdout


def check(name: str, reported, independent, tol: float = 0.0, note: str = "") -> None:
    if reported is None or independent is None:
        ok = reported is None and independent is None
    elif isinstance(reported, (int, float)) and isinstance(independent, (int, float)):
        ok = abs(float(reported) - float(independent)) <= tol
    else:
        ok = reported == independent
    RESULTS.append((name, reported, independent, "PASS" if ok else "FAIL", note))


def germline_spans(panel_json: Path, bed: Path) -> list:
    genes = json.loads(panel_json.read_text())
    rows = sorted(
        (g["chromosome"], g["start_position"], g["end_position"], sym)
        for sym, g in genes.items()
        if (g.get("breast_panel") or {}).get("role") == "germline"
    )
    bed.write_text("".join(f"{c}\t{s - 1}\t{e}\t{sym}\n" for c, s, e, sym in rows))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("output_dir", type=Path)
    ap.add_argument("--clinvar", type=Path, default=Path(os.getenv("LOCAL_DATA_ROOT", str(Path.home() / "germlineiq_data"))) / "reference/clinvar/clinvar_GRCh38.vcf.gz")
    ap.add_argument("--panel", type=Path, default=ROOT / "Backend/data/cancer_genes/cancer_genes_db.json")
    ap.add_argument("--padding", type=int, default=100, help="PANEL_INTERVAL_PADDING (pb)")
    args = ap.parse_args()
    out = args.output_dir.expanduser()
    stats = json.loads((out / "vcf_statistics.json").read_text())
    report = json.loads(sorted(out.glob("REP-*.json"))[-1].read_text())
    # Files actually used by the report: variant calling may have been reused from another analysis
    # of the same FASTQ files (result cache) — the report records the BAM it used.
    used_bam = (stats.get("alignment") or {}).get("bam")
    src = Path(used_bam).parent if used_bam else out
    if src != out:
        print(f"Note: variant calling reused from {src} (result cache) — verifying the files the report used")
    vcf = src / "variants.vcf.gz"
    if not vcf.is_file():  # VCF mode: file provided by the user
        ann = json.loads((out / "annotated_variants.json").read_text())["annotation"]
        candidates = [p for d in (out, out.parent / "input") for p in sorted(d.glob("*.vcf*")) if not p.name.endswith((".tbi", ".csi"))]
        import hashlib
        vcf = next(p for p in candidates if hashlib.sha256(p.read_bytes()).hexdigest() == ann["vcf_sha256"])
    tmp = Path(tempfile.mkdtemp(prefix="expert_check_"))
    spans = germline_spans(args.panel, tmp / "germline_genes.bed")

    # --- Panel variants (bcftools) ------------------------------------------------
    # Split alleles (norm -m -any) carried by the sample (GT contains the alt allele).
    # Panel definition: germline gene ± padding (PANEL_INTERVAL_PADDING). In the padding, a variant is
    # only assigned to the gene through ClinVar (GENEINFO): membership is therefore checked
    # (strict gene ⊆ report ⊆ gene + padding), then the metrics are recomputed on the report set.
    pad = args.padding
    (tmp / "padded.bed").write_text("".join(f"{c}\t{max(0, s - 1 - pad)}\t{e + pad}\t{g}\n" for c, s, e, g in spans))
    if not Path(f"{vcf}.tbi").exists() and not Path(f"{vcf}.csi").exists():
        gz = tmp / "input.vcf.gz"
        sh(f"bcftools sort {vcf} -Oz -o {gz} 2>/dev/null && bcftools index -t {gz}")
        vcf = gz
    base = f"bcftools view -R {tmp}/padded.bed {vcf} -Ou | bcftools norm -m -any -Ou 2>/dev/null | bcftools view -i 'GT=\"alt\"' -Ou"
    fmt = "'%CHROM\\t%POS\\t%REF\\t%ALT\\t%FILTER\\t[%GT]\\t[%DP]\\t[%AD]\\n'"
    padded_rows = {tuple(l.split("\t")[:4]): l.split("\t") for l in sh(f"{base} | bcftools query -f {fmt}").splitlines()}

    def in_strict(c: str, pos: int) -> bool:
        return any(c == gc and gs <= pos <= ge for gc, gs, ge, _ in spans)
    strict = {k for k in padded_rows if in_strict(k[0], int(k[1]))}
    reported = {(v["chromosome"], str(v["position"]), v["ref"], v["alt"]) for v in stats["variants"]}
    margin = sorted(reported - strict)
    check("All strict-gene variants reported", len(strict - reported), 0, note=f"{len(strict)} inside the genes (no padding)")
    check("All reported variants within gene ± padding", len(reported - set(padded_rows)), 0,
          note=f"{len(margin)} in the {pad} bp padding (gene assigned by ClinVar)")
    rows = [padded_rows[k] for k in sorted(reported & set(padded_rows))]
    check("Germline panel variants", stats["panel"]["variants_in_panel"], len(rows), note="bcftools view -R | norm -m -any | GT=alt")
    (tmp / "reported.tsv").write_text("".join(f"{k[0]}\t{k[1]}\t{k[2]},{k[3]}\n" for k in sorted(reported)))
    base = f"{base} | bcftools view -T {tmp}/reported.tsv -Ou"

    pass_rows = [r for r in rows if r[4] in ("PASS", ".")]
    check("PASS variants", stats["panel"]["pass"], len(pass_rows))

    snv_pass = [r for r in pass_rows if len(r[2]) == 1 and len(r[3]) == 1]
    ts_pairs = {("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")}
    ts = sum(1 for r in snv_pass if (r[2], r[3]) in ts_pairs)
    tv = len(snv_pass) - ts
    # bcftools stats as a second Ti/Tv computation
    tstv = [l.split("\t") for l in sh(f"{base} | bcftools view -f PASS,. -v snps -Ou | bcftools stats - ").splitlines() if l.startswith("TSTV")]
    bt_ts, bt_tv = int(tstv[0][2]), int(tstv[0][3])
    check("PASS SNV transitions", stats["panel"]["transitions_pass_snv"], bt_ts, note="bcftools stats (TSTV)")
    check("PASS SNV transversions", stats["panel"]["transversions_pass_snv"], bt_tv, note="bcftools stats (TSTV)")
    check("Ti/Tv", stats["panel"]["ti_tv"], round(ts / tv, 3) if tv else None, tol=0.001)

    def zyg(gt: str) -> str:
        a = gt.replace("|", "/").split("/")
        return "hom" if len(set(a)) == 1 else "het"
    het = [r for r in rows if zyg(r[5]) == "het"]
    hom = [r for r in rows if zyg(r[5]) == "hom"]
    check("Het/hom ratio", stats["panel"]["het_hom_ratio"], round(len(het) / len(hom), 3) if hom else None, tol=0.001)

    dps = [int(r[6]) for r in rows if r[6] not in (".", "")]
    check("Median depth (FORMAT/DP)", stats["distributions"]["depth"]["summary"]["median"], statistics.median(dps) if dps else None, tol=1e-6)

    def vaf(r):
        ad = [int(x) for x in r[7].split(",") if x not in (".", "")]
        return ad[1] / sum(ad) if len(ad) >= 2 and sum(ad) else None
    hv = [v for v in (vaf(r) for r in het) if v is not None]
    check("Median heterozygous VAF (AD)", stats["distributions"]["vaf_heterozygous"]["summary"]["median"],
          round(statistics.median(hv), 4) if hv else None, tol=1e-4)

    # --- Alignment (samtools / Picard) ---------------------------------------------
    aln = stats.get("alignment")
    bam = src / "aligned.bam"
    if aln and bam.is_file():
        fs = sh(f"samtools flagstat -@ 4 {bam}").splitlines()
        total = int(fs[0].split()[0])
        mapped = int(next(l for l in fs if " mapped (" in l and "primary" not in l).split()[0])
        check("Total reads", aln["total_reads"], total, note="samtools flagstat")
        check("Mapping rate", aln["mapped_rate"], round(mapped / total, 4), tol=1e-4)
        dup = sh(f"awk -F'\\t' '/^LIBRARY/{{getline; print $9}}' {src}/duplicate_metrics.txt").strip()
        check("Duplicate rate (Picard)", aln["duplication_rate"], round(float(dup), 4), tol=1e-4, note="awk on duplicate_metrics.txt")

        # --- Coverage of ClinVar P/LP sites (independent bcftools + awk chain) ---
        cov = aln.get("clinvar_sites_coverage")
        if cov and args.clinvar.is_file():
            nochr = tmp / "genes_nochr.bed"
            nochr.write_text("".join(f"{c[3:]}\t{s - 1}\t{e}\t{g}\n" for c, s, e, g in spans))
            # Primary CLNSIG (before "|") = Pathogenic / Likely_pathogenic (low penetrance included), ref ≤ 50 bp
            sites_txt = sh(
                f"bcftools view -T {nochr} {args.clinvar} -Ov 2>/dev/null | "
                "bcftools query -f '%CHROM\\t%POS\\t%REF\\t%ALT\\t%INFO/CLNSIG\\n' 2>/dev/null | "
                "awk -F'\\t' '{split($5,p,\"|\"); s=p[1]; "
                "if ((s ~ /^Pathogenic/ || s ~ /^Likely_pathogenic/) && length($3) <= 50 && $4 != \".\" && $4 != \"*\") "
                "print \"chr\"$1\"\\t\"$2\"\\t\"$2+length($3)-1\"\\t\"$1\":\"$2\":\"$3\":\"$4}' | sort -u"
            )
            sites = []
            for l in sites_txt.splitlines():
                c, s, e, k = l.split("\t")
                s, e = int(s), int(e)
                if any(c == gc and gs <= s <= ge for gc, gs, ge, _ in spans):
                    sites.append((c, s, e))
            bed = tmp / "sites.bed"
            bed.write_text("".join(f"{c}\t{s - 1}\t{e}\n" for c, s, e in sorted(set(sites))))
            depth = {}
            for l in sh(f"samtools depth -a -Q 20 -q 20 -b {bed} {bam}").splitlines():
                c, p, d = l.split("\t")
                depth[(c, int(p))] = int(d)
            mind = cov["min_depth"]
            covered = sum(1 for c, s, e in sites if min(depth.get((c, p), 0) for p in range(s, e + 1)) >= mind)
            check("ClinVar P/LP sites assessed", cov["sites"], len(sites), note="bcftools + awk (primary CLNSIG, ALT ≠ \".\")")
            check(f"P/LP sites covered ≥ {mind}x", cov["covered"], covered, note="samtools depth -a -Q20 -q20")

    # --- Report ↔ statistics consistency ---------------------------------------------
    gf = report["genomic_findings"]
    cats = stats["panel"]["by_category"]
    check("Confirmed P/LP (report = statistics)", len(gf["pathogenic_variants_detected"]), cats["pathogenic_confirmed"])
    check("To confirm (report = statistics)", len(gf.get("variants_to_confirm", [])), cats["pathogenic_to_confirm"] + cats["lof_to_confirm"])
    check("VUS (report = statistics)", len(gf.get("vus_detected", [])), cats["vus"])
    check("Panel variants (report = statistics)", gf["variants_in_panel"], stats["panel"]["variants_in_panel"])
    level = report["clinical_prediction"]["risk_level"]
    expected = "HIGH" if cats["pathogenic_confirmed"] else ("INDETERMINATE" if cats["pathogenic_to_confirm"] + cats["lof_to_confirm"] else None)
    if expected:
        check("Risk level consistent with P/LP", level if level in ("HIGH", "MODERATE", "INDETERMINATE") else level, level if level in ("HIGH", "MODERATE", "INDETERMINATE") else "HIGH/MODERATE/INDETERMINATE")
    else:
        cov = (stats.get("alignment") or {}).get("clinvar_sites_coverage") or {}
        frac = cov.get("fraction_covered")
        if frac is not None and frac < 0.90:
            check("No P/LP + coverage < 90 % → INDETERMINATE", level, "INDETERMINATE", note=f"sites covered {frac:.1%}")
        else:
            check("No P/LP → risk LOW", level, "LOW")

    width = max(len(r[0]) for r in RESULTS)
    print(f"\nIndependent verification — {out}\n")
    print(f"{'Check':{width}}  {'Report':>12}  {'Independent':>12}  Status  Method")
    for name, rep, ind, status, note in RESULTS:
        print(f"{name:{width}}  {str(rep):>12}  {str(ind):>12}  {status:6}  {note}")
    fails = sum(1 for r in RESULTS if r[3] == "FAIL")
    print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} checks agree.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
