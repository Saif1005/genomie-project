# GermlineIQ

**Multi-agent clinical genomics platform for hereditary breast cancer risk.**

GermlineIQ turns sequencing data (FASTQ or VCF) into a **hereditary breast cancer risk report**, on a
**local server** (no cloud service), with LangGraph multi-agent orchestration, a GATK pipeline
(GPU-accelerated with NVIDIA Parabricks when available), deterministic statistics and risk rules,
a verified BioGPT literature commentary and a clinical web interface.

> **Disclaimer** — Research software. Results are not a medical diagnosis and must be validated by a
> clinical geneticist or an oncologist.

Installation and operation: **[DEPLOY_LOCAL.md](DEPLOY_LOCAL.md)** · Detailed architecture:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

**Demonstration & validation report (PDF, 45 pages):** [docs/GermlineIQ_Demo_Validation_Report.pdf](docs/GermlineIQ_Demo_Validation_Report.pdf) —
screenshots of every screen, technical description of each tool, benchmarks, biological interpretation of the
findings and research contributions.

---

## What the system does

| Input | Agent chain | Output |
|---|---|---|
| **FASTQ** R1 + R2 | preparation → variant calling (Parabricks GPU or GATK4 CPU) + alignment QC → ClinVar annotation → QC, classification and statistics → risk → report | Clinical JSON report |
| **VCF** | ClinVar annotation → QC, classification and statistics → risk → report | Clinical JSON report |

The orchestrator computes the plan dynamically from the data provided (a VCF skips alignment;
variant calling already done on the same FASTQ files is reused).

### Principles

- **Deterministic**: the risk level is decided by explicit, versioned rules (`germlineiq-rules-v1.1`),
  never by a language model. Same input + same panel + same ClinVar release → same report, with the
  SHA-256 of the input and every version written into the report. Verified by the benchmark.
- **Conservative**: without ClinVar annotation the analysis refuses to conclude; a pathogenic variant
  of insufficient quality gives an **INDETERMINATE** risk (confirmation required); in FASTQ mode,
  insufficient coverage of known pathogenic sites also gives INDETERMINATE — never a false "low risk".
- **Verified AI**: BioGPT writes a literature commentary (checked against curated ClinGen/NCCN
  gene–disease associations) and, fine-tuned on VCF statistics, an interpretation of the
  statistics of each analysis; every sentence is checked by a deterministic verifier against the
  computed statistics and replaced by a reference sentence when it does not match.
- **Auditable**: every agent writes a JSON artifact; `scripts/expert_check.py` recomputes every
  report metric with bcftools/samtools, independently of the backend code.
- **Public data**: hg38 and known sites (GATK Resource Bundle, Broad Institute), ClinVar (NCBI,
  public domain), gene coordinates (Ensembl / NCBI Gene).
- **Local**: health data stay on the server disk, ports bound to `127.0.0.1` by default.

---

## Hereditary breast cancer panel

| Penetrance | Genes | Risk if a pathogenic variant is confirmed |
|---|---|---|
| High | BRCA1, BRCA2, PALB2, TP53, PTEN, CDH1, STK11 | HIGH |
| Moderate | CHEK2, ATM, BARD1, RAD51C, RAD51D, NF1 | MODERATE |
| Somatic (not used for germline risk) | PIK3CA, ERBB2/HER2, MYC | — |

GRCh38 coordinates and roles: `Backend/data/cancer_genes/cancer_genes_db.json`.

| Level | Rule (`germlineiq-rules-v1.1`) |
|---|---|
| HIGH | ≥ 1 confirmed P/LP variant (ClinVar) in a high-penetrance gene |
| MODERATE | ≥ 1 confirmed P/LP variant in a moderate-penetrance gene, or a "low penetrance" allele |
| INDETERMINATE | none confirmed but a variant to confirm (QUAL < 30, DP < 15, VAF outside 0.25–0.75, GATK filter, unclassified loss of function) — or, in FASTQ mode, < 90 % of the panel's known pathogenic sites covered ≥ 15× |
| LOW | no P/LP variant and nothing to confirm in the 13 germline genes; genes with < 95 % of their pathogenic sites covered are named "not excluded" |

Limitations (repeated in every report): large rearrangements / CNVs are not detected, classification
depends on ClinVar, coverage is only measurable in FASTQ mode.

---

## Architecture

```
Next.js frontend (:3000) ──/api/v1 (proxy)──▶ FastAPI backend (:8000)
                                                 │
                                   LangGraph orchestrator: plan ⇄ execute
                                   tool registry · planner · router (rules or Mistral) · cache
                                                 │
   DataManager → VariantCalling → VariantAnnotation → VCFAnalysis → Prediction → Report
                  (Parabricks/GATK4    (ClinVar)       (QC, stats)  (rules + verified BioGPT)
                   + alignment QC)
                                                 │
                         LOCAL_DATA_ROOT: reference, ClinVar, patients, models, benchmarks
                         Ollama (Mistral: assistant/router) · GATK/Parabricks containers
```

| Layer | Technologies |
|---|---|
| Interface | Next.js 14, TypeScript, Tailwind CSS |
| API | FastAPI, Pydantic v2 |
| Orchestration | LangGraph, MCP server (stdio) |
| Genomics | BWA-MEM, GATK 4.2, NVIDIA Parabricks 4.6, samtools, bcftools |
| Interpretation | Deterministic rules + ClinVar; BioGPT (verified, non-decisional commentary; LoRA fine-tuning pipeline) |
| Assistant | Mistral v0.3 through Ollama |
| Deployment | Docker Compose or native conda, local server |

### Repository layout

```
Backend/
├── app/                  API: main.py, schemas.py, jobs.py (queue + persisted state), routers/
├── config/settings.py    Single configuration source (Backend/.env)
├── src/
│   ├── core/             Agent contract, context vocabulary
│   ├── orchestration/    Tool registry, planner, router, LangGraph engine, cache
│   ├── agents/           One agent = one tool (data_manager, variant_calling, annotation, …)
│   ├── genomics/         Pure scientific domain: panel, VCF reading, ClinVar, QC, statistics, risk
│   ├── pipeline/         GATK/Parabricks commands, execution, resume, alignment QC
│   ├── llm/              BioGPT (verified commentary, corpus, LoRA fine-tuning, evaluation), Ollama client
│   ├── report/, schemas/ Clinical report
│   ├── mcp/              MCP server
│   ├── storage/          Patient storage (LOCAL_DATA_ROOT)
│   └── utils/            GPU/VRAM, input validation
├── benchmarks/           Multi-agent benchmark (multiagent.py) + API latency/load tools
├── data/cancer_genes/    Gene panel
└── tests/                unit/, integration/, fixtures/ (synthetic VCFs)
Frontend/                 Clinical interface: Analyze, Cohort, Benchmarks, Architecture
scripts/                  start/stop (Docker or native), check_prereqs, download_reference, pull_models,
                          smoke_test, expert_check, 1000 Genomes cohort tools
docker-compose.local.yml  Local stack (+ docker-compose.gpu.yml with a GPU)
```

---

## Quick start

```bash
bash scripts/check_prereqs.sh                       # server diagnostics
bash scripts/start.sh                                # build and start the stack (or scripts/start_native.sh)
bash scripts/download_reference.sh --clinvar-only    # ClinVar (~0.2 GB): enough for VCFs
bash scripts/smoke_test.sh                           # end-to-end check
bash scripts/install_autostart.sh                    # native mode: start automatically (systemd user service)
```

Then open **http://localhost:3000** in the server's browser. For FASTQ mode:
`bash scripts/download_reference.sh` (hg38, ~9.7 GB) and `bash scripts/pull_models.sh`.

Backend tests: `cd Backend && pip install -r requirements-dev.txt && python -m pytest` (129 tests).

### REST API

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Health, selected GPU/CPU engine, panel, ClinVar availability |
| GET | `/api/v1/tools` | Available agents (inputs / outputs) |
| POST | `/api/v1/analyze` | FASTQ analysis (`fastq_r1`, `fastq_r2`: server paths) |
| POST | `/api/v1/analyze/vcf` | VCF analysis (`vcf_path`) |
| POST | `/api/v1/analyze/upload` | FASTQ upload then analysis |
| GET | `/api/v1/jobs` | Job history (risk, genes, duration) |
| GET | `/api/v1/jobs/{id}` | Status, plan, per-agent timings |
| GET | `/api/v1/jobs/{id}/report` | Clinical JSON report (with statistics and QC) |
| GET | `/api/v1/models/biogpt` | Commentary model, fine-tuning history, verification knowledge |
| GET | `/api/v1/benchmarks/latest` | Latest multi-agent benchmark results |
| POST | `/api/v1/assistant/chat` | Conversational assistant |

---

## Benchmark results

Measured with `python -m benchmarks.multiagent --suites all` on the delivery server
(WSL2, 6 CPUs, 17 GB RAM, RTX 5080 16 GB, Parabricks 4.6 on GPU, Mistral router). Full results in the
**Benchmarks** page of the interface.

**Clinical accuracy — real open-access cohort (1000 Genomes Project, 30× Illumina reads over the panel genes)**

| Sample | Ground truth (ClinVar P/LP) | Expected → reported risk |
|---|---|---|
| NA19130 | BRCA2 insertion (expert panel) | HIGH → **HIGH** |
| HG00611 | BRCA2 duplication (expert panel) | HIGH → **HIGH** |
| HG02620 | PALB2 deletion | HIGH → **HIGH** |
| NA10842 | CHEK2 c.1100delC | MODERATE → **MODERATE** |
| HG00596 | ATM | MODERATE → **MODERATE** |
| HG00096 | none (control) | LOW → **LOW** |
| HG01112 | none (control) | LOW → **LOW** |

- Carrier sensitivity 5/5, control specificity 2/2, risk-level accuracy 7/7 (n = 7: 95 % CI 65–100 %).
- 0 unexpected confirmed pathogenic calls. One low-VAF TP53 p.Arg273His call (c.818G>A, rs28934576; 5/36 reads) in NA10842 —
  absent from the germline call set, typical of a culture-acquired or clonal-haematopoiesis mutation in
  cell-line DNA — was correctly reported "to confirm", not as a germline finding.
- End-to-end FASTQ → report: median 99 s per sample (≈ 150k read pairs, Parabricks GPU; 93 s with GATK4
  on CPU — on inputs this small the fixed GPU start-up cost dominates). Parabricks and GATK4 give identical
  final VCFs (NA10842: 1,357/1,357 calls).

**Analytical validity — GIAB HG001 (NA12878 exome), panel regions covered ≥ 15×**

| Calls | Precision | Recall | F1 |
|---|---|---|---|
| PASS (GATK hard filters) | 97.5 % | 88.8 % | 0.929 |
| All HaplotypeCaller calls | 93.7 % | 100 % | 0.967 |

Missed PASS calls were all detected by HaplotypeCaller and removed by hard filters (mostly the SOR
strand-bias filter, a known exome-capture artefact); pathogenic variants in filtered calls are still
reported "to confirm".

**System properties**

- Reproducibility: identical clinical reports and statistics over repeated runs (VCF and FASTQ).
- Orchestration: deterministic and Mistral routers produce the same plan and the same clinical
  report; orchestration overhead ≈ 0.17 s per analysis.
- Robustness: 9/9 fault-injection cases (invalid ID, path traversal, missing file, identical R1/R2,
  S3 URI, malformed VCF…) handled cleanly, service healthy afterwards.
- API latency: p95 < 1.1 ms on read endpoints.
- BioGPT LoRA fine-tuning (4 documented iterations on 2,001 frozen PubMed abstracts): test perplexity
  15.8 → 8.6, but no adapter passed the factual-accuracy promotion gate on held-out prompts; production
  uses base BioGPT with sentence-level verification.
- BioGPT fine-tuned on VCF statistics (LoRA, 4,000 synthetic statistics files, 4 min on the RTX 5080),
  evaluated through the deterministic verifier:

  | Test set | Model | Verified sentences | Topics covered | Numbers correct | Wrong risk levels |
  |---|---|---|---|---|---|
  | 300 synthetic files (incl. abnormal QC) | base BioGPT | 0 % | 0 % | — | 2 / 40 |
  | 300 synthetic files (incl. abnormal QC) | fine-tuned | 98.6 % | 98.6 % | 100 % | 0 |
  | 15 real analyses (held out) | fine-tuned | 100 % | 100 % | 100 % | 0 |

  Promoted (v1). The remaining errors (a wrong status word on 1.4 % of sentences) are caught by the
  verifier and replaced by the reference sentence: every final answer is verified.

---

## Roadmap

- CNV detection (large BRCA1/2 rearrangements)
- Larger validation cohorts (GIAB HG002–HG007, GeT-RM positive controls), hap.py stratified metrics
- FHIR / hospital EHR integration; regulatory pathway (IVDR)

---

## Licence and use

Academic research project (doctoral work in computer science and bioinformatics).
**Do not use in clinical production without medical and regulatory validation.**
