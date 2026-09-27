#!/usr/bin/env python3
"""Build a real demonstration cohort from 1000 Genomes 30x sequencing reads (open access, IGSR).

For each selected individual, the real Illumina reads overlapping the 13 germline panel genes
(± 1 kb) are streamed from the public CRAM (EBI), converted back to paired FASTQ and placed in
patients/<SAMPLE>/input/, ready for the full FASTQ → report pipeline. The expected result
(ground truth) comes from the independent carrier scan (scripts/find_1000g_carriers.py).

Usage (conda env "genomic"):
    python scripts/prepare_1000g_cohort.py --carriers ~/germlineiq_data/reference/1000g/carriers.tsv
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path.home() / "germlineiq_data"
INDEX = "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections/1000G_2504_high_coverage/{name}"
INDEX_FILES = ("1000G_2504_high_coverage.sequence.index", "1000G_698_related_high_coverage.sequence.index")
PAD = 1000

# Cohort: real carriers (expected MODERATE/HIGH) and non-carriers (expected LOW)
COHORT = {
    "NA19130": "BRCA2 pathogenic insertion (ClinVar expert panel)",
    "HG00611": "BRCA2 pathogenic duplication (ClinVar expert panel)",
    "HG02620": "PALB2 pathogenic deletion",
    "NA10842": "CHEK2 c.1100delC founder variant",
    "HG00596": "ATM pathogenic variant",
    "HG00096": "No ClinVar P/LP variant in the panel (control)",
    "HG01112": "No ClinVar P/LP variant in the panel (control)",
}


def sh(cmd: str) -> str:
    p = subprocess.run(["bash", "-c", f"set -o pipefail; {cmd}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd}\n{p.stderr[-800:]}")
    return p.stdout


def cram_urls() -> dict:
    urls = {}
    for name in INDEX_FILES:
        for line in sh(f"curl -fsSL {INDEX.format(name=name)}").splitlines():
            if line.startswith("#"):
                continue
            path = line.split("\t")[0]
            if path.endswith(".cram"):
                sample = path.rsplit("/", 1)[1].split(".")[0]
                urls[sample] = path.replace("ftp://", "https://")
    return urls


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--carriers", type=Path, required=True)
    ap.add_argument("--reference", type=Path, default=DATA / "reference/hg38/hg38.fa")
    ap.add_argument("--panel", type=Path, default=ROOT / "Backend/data/cancer_genes/cancer_genes_db.json")
    ap.add_argument("--samples", nargs="*", default=list(COHORT))
    args = ap.parse_args()

    genes = {s: g for s, g in json.loads(args.panel.read_text()).items() if (g.get("breast_panel") or {}).get("role") == "germline"}
    bed = DATA / "reference/1000g/panel_genes_pad1kb.bed"
    bed.parent.mkdir(parents=True, exist_ok=True)
    bed.write_text("".join(f"{g['chromosome']}\t{g['start_position'] - 1 - PAD}\t{g['end_position'] + PAD}\t{s}\n"
                           for s, g in sorted(genes.items(), key=lambda kv: (kv[1]["chromosome"], kv[1]["start_position"]))))
    with open(args.carriers) as fh:
        carriers = list(csv.DictReader(fh, delimiter="\t"))
    urls = cram_urls()

    manifest = []
    for sample in args.samples:
        url = urls[sample]
        inp = DATA / "patients" / sample / "input"
        inp.mkdir(parents=True, exist_ok=True)
        r1, r2 = inp / f"{sample}_R1.fastq.gz", inp / f"{sample}_R2.fastq.gz"
        if not (r1.is_file() and r2.is_file()):
            print(f"{sample}: streaming reads from {url}", file=sys.stderr)
            tmp_bam = inp / f".{sample}.panel.bam"
            for attempt in range(1, 5):  # remote seeks over HTTPS occasionally fail: retry
                try:
                    sh(f"samtools view -@4 -b -M -T {args.reference} -L {bed} -o {tmp_bam} '{url}'")  # -M: index jumps
                    break
                except RuntimeError as e:
                    if attempt == 4:
                        raise
                    print(f"{sample}: attempt {attempt} failed ({str(e).splitlines()[-1]}), retrying", file=sys.stderr)
            # original read pairs back to FASTQ (pairs with both mates in the regions)
            sh(f"samtools collate -@4 -u -O {tmp_bam} | samtools fastq -@4 -n -1 {r1} -2 {r2} -0 /dev/null -s /dev/null -")
            tmp_bam.unlink()
        pairs = int(sh(f"zcat {r1} | wc -l")) // 4
        expected = [c for c in carriers if c["sample"] == sample]
        manifest.append({
            "sample": sample,
            "description": COHORT.get(sample, ""),
            "source": url,
            "read_pairs": pairs,
            "fastq_r1": str(r1),
            "fastq_r2": str(r2),
            "expected_plp": [{k: c[k] for k in ("gene", "variant", "genotype", "clnsig", "revstat", "hgvs")} for c in expected],
        })
        print(f"{sample}: {pairs} read pairs, expected P/LP: {[e['gene'] for e in expected] or 'none'}", file=sys.stderr)

    out = DATA / "reference/1000g/cohort_manifest.json"
    out.write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "1000 Genomes Project, 30x high-coverage (NYGC), open access — IGSR",
        "regions": str(bed),
        "samples": manifest,
    }, indent=2))
    print(f"Manifest: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
