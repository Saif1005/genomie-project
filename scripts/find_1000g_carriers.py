#!/usr/bin/env python3
"""Find real carriers of ClinVar pathogenic variants in the 1000 Genomes Project (30x, 3,202 genomes).

Open-access, consented public data (IGSR). For each germline gene of the panel, the phased
high-coverage VCF is queried remotely by region (tabix over HTTPS), every allele is matched
against the local ClinVar release (Pathogenic / Likely pathogenic, primary CLNSIG), and the
carrier genotypes are listed.

Usage (conda env "genomic"):
    python scripts/find_1000g_carriers.py --out ~/germlineiq_data/reference/1000g/carriers.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ("https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/"
        "working/20220422_3202_phased_SNV_INDEL_SV")
VCF = BASE + "/1kGP_high_coverage_Illumina.{chrom}.filtered.SNV_INDEL_SV_phased_panel.vcf.gz"
PED = ("https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/"
       "20130606_g1k_3202_samples_ped_population.txt")


def sh(cmd: str) -> str:
    p = subprocess.run(["bash", "-c", f"set -o pipefail; {cmd}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd}\n{p.stderr[-600:]}")
    return p.stdout


def clinvar_plp(clinvar: Path, genes: dict) -> dict:
    """{'chr17:43045712:T:C': {'gene', 'clnsig', 'revstat', 'conditions', 'hgvs', 'rsid', 'vcv'}}"""
    regions = "\n".join(f"{g['chromosome'][3:]}\t{g['start_position']}\t{g['end_position']}" for g in genes.values())
    bed = Path("/tmp/germlineiq_genes_nochr.tsv")
    bed.write_text(regions + "\n")
    out = sh(
        f"bcftools view -T {bed} {clinvar} -Ov 2>/dev/null | "
        "bcftools query -f '%CHROM\\t%POS\\t%ID\\t%REF\\t%ALT\\t%INFO/CLNSIG\\t%INFO/CLNREVSTAT\\t%INFO/CLNDN\\t%INFO/CLNHGVS\\t%INFO/RS\\n' 2>/dev/null"
    )
    sites = {}
    for line in out.splitlines():
        c, pos, vcv, ref, alt, sig, rev, dn, hgvs, rs = line.split("\t")
        primary = sig.split("|")[0]
        if alt in (".", "*") or not (primary.startswith("Pathogenic") or primary.startswith("Likely_pathogenic")):
            continue
        gene = next((s for s, g in genes.items() if g["chromosome"] == f"chr{c}" and g["start_position"] <= int(pos) <= g["end_position"]), None)
        if gene:
            sites[f"chr{c}:{pos}:{ref}:{alt}"] = {
                "gene": gene, "clnsig": sig, "revstat": rev, "conditions": dn.replace("_", " "),
                "hgvs": hgvs, "rsid": f"rs{rs}" if rs not in (".", "") else "", "vcv": vcv,
            }
    return sites


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clinvar", type=Path, default=Path.home() / "germlineiq_data/reference/clinvar/clinvar_GRCh38.vcf.gz")
    ap.add_argument("--panel", type=Path, default=ROOT / "Backend/data/cancer_genes/cancer_genes_db.json")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    genes = {s: g for s, g in json.loads(args.panel.read_text()).items() if (g.get("breast_panel") or {}).get("role") == "germline"}
    plp = clinvar_plp(args.clinvar, genes)
    print(f"ClinVar P/LP alleles in the {len(genes)} germline genes: {len(plp)}", file=sys.stderr)

    ped = {}
    with urllib.request.urlopen(PED, timeout=60) as r:
        for row in csv.DictReader(r.read().decode().splitlines(), delimiter=" "):
            ped[row["SampleID"]] = row

    carriers = []
    for sym, g in sorted(genes.items()):
        region = f"{g['chromosome']}:{g['start_position']}-{g['end_position']}"
        url = VCF.format(chrom=g["chromosome"])
        # sites-only first (fast), genotypes only for matching alleles
        sites = sh(f"bcftools view -G -r {region} '{url}' 2>/dev/null | bcftools norm -m -any 2>/dev/null | bcftools query -f '%CHROM\\t%POS\\t%REF\\t%ALT\\n'")
        hits = [l for l in sites.splitlines() if ":".join(l.split("\t")) in plp]
        print(f"{sym:7s} {region:28s} {len(sites.splitlines()):6d} sites, {len(hits)} ClinVar P/LP", file=sys.stderr)
        for h in hits:
            c, pos, ref, alt = h.split("\t")
            gts = sh(
                f"bcftools view -r {c}:{pos}-{pos} '{url}' 2>/dev/null | bcftools norm -m -any 2>/dev/null | "
                f"bcftools view -i 'REF=\"{ref}\" && ALT=\"{alt}\"' 2>/dev/null | "
                "bcftools query -i 'GT=\"alt\"' -f '[%SAMPLE\\t%GT\\n]'"
            )
            key = f"{c}:{pos}:{ref}:{alt}"
            for line in gts.splitlines():
                sample, gt = line.split("\t")
                meta = ped.get(sample, {})
                carriers.append({"sample": sample, "population": meta.get("Population", ""), "superpopulation": meta.get("Superpopulation", ""),
                                 "sex": {"1": "male", "2": "female"}.get(meta.get("Sex", ""), ""), "genotype": gt, "variant": key, **plp[key]})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["sample", "population", "superpopulation", "sex", "gene", "variant", "genotype", "clnsig", "revstat", "hgvs", "rsid", "vcv", "conditions"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in sorted(carriers, key=lambda r: (r["gene"], r["variant"], r["sample"])):
            w.writerow(row)
    print(f"{len(carriers)} carrier genotypes in {len({r['sample'] for r in carriers})} individuals → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
