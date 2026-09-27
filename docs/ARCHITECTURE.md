# GermlineIQ backend architecture

Written for developers who maintain or extend the backend.

## 1. Layers and dependency rules

```
app/                  HTTP API: input validation, jobs, routers
  └─▶ src/orchestration   multi-agent engine (registry, planner, router, cache)
        └─▶ src/agents     one agent = one tool; reads/writes the context
              ├─▶ src/genomics   pure scientific domain (no network I/O, no LLM)
              ├─▶ src/pipeline   GATK/Parabricks commands, execution, resume, alignment QC
              ├─▶ src/llm        BioGPT (verified commentary, fine-tuning), Ollama
              └─▶ src/report     report assembly
src/core     shared contracts (BaseAgent, context keys) — depends on nothing
config/settings.py   single source of configuration (environment variables)
```

- A layer only imports the layers **below** it.
- `src/genomics` depends only on itself and `config`, and stays testable without Docker, GPU or network.
- Heavy dependencies (torch, transformers) are imported **inside the functions** that use them:
  the API starts without them.

## 2. Shared context

Agents communicate through a JSON-serialisable dictionary whose keys are defined in
`src/core/context.py`. An agent reads its inputs and returns **only** its outputs in
`AgentResult.data`; the engine merges them.

| Key | Produced by | Content |
|---|---|---|
| `patient_id`, `fastq_r1`, `fastq_r2`, `vcf_uri`, `train_llm` | user | inputs |
| `fastq_r1_uri`, `fastq_r2_uri` | data_manager | FASTQ stored in `patients/<ID>/input` |
| `vcf_uri`, `bam_uri`, `pipeline_backend`, `alignment_qc` | genomic_pipeline | filtered VCF, BAM, engine used, alignment QC |
| `annotated_variants_path`, `annotation`, `input_sha256` | variant_annotation | JSON artifact, ClinVar release |
| `panel_analysis`, `vcf_metrics`, `vcf_statistics` | vcf_analysis | confirmed / to-confirm / VUS variants, statistics |
| `risk_assessment`, `prediction_results` | prediction | level, rationale, verified commentary |
| `clinical_report`, `report_uri` | report | final report |

Each agent also writes an **artifact** in `patients/<ID>/output/` (sorted JSON, hence comparable
with `diff`), so every step can be audited or replayed.

## 3. Orchestration (src/orchestration)

**Registry (`registry.py`)**: each tool declares `requires`, `produces`, its GPU phase, whether it is
exclusive, whether it is critical and which inputs identify a reusable result. It is the single
source for the planner, the MCP server (`tools/list`) and `GET /api/v1/tools`.

**Planner (`planner.py`)**: backward chaining from the goals (`clinical_report`, plus
`training_data_path` if `train_llm`). Data already present are not recomputed:

| Context provided | Plan |
|---|---|
| FASTQ | data_manager → genomic_pipeline → variant_annotation → vcf_analysis → prediction → report |
| VCF | variant_annotation → vcf_analysis → prediction → report |
| VCF + `train_llm` | … → report → llm_training |

**Engine (`engine.py`)**: LangGraph graph with two nodes, `plan` then `execute`, in a loop.
- At every turn the plan is **recomputed** from the current context.
- If several non-exclusive tools are ready, they run in parallel and their outputs are merged in
  registry order.
- Exclusive (GPU) tools run alone; the router picks among the candidates.
- The failure of a critical tool stops the plan. The failure of a non-critical tool (`llm_training`)
  is logged and its goal waived.
- GPU hooks free VRAM before Parabricks and before BioGPT (`GPUManager`).
- Every step is recorded (`StepRecord`: tool, status completed/cached/failed, duration); the job API
  exposes these timings (`step_timings`, `duration_s`, `router`).

**Router (`router.py`)**: registry order by default. With `ORCHESTRATOR_DETERMINISTIC=false`, Mistral
picks among the ready tools. Any invalid answer falls back to registry order. The choice cannot
change the clinical result, because the candidate tools are independent of each other (verified by
the orchestration benchmark suite).

**Cache (`cache.py`)**: variant calling is reused if none of these changed:
- the FASTQ files (path, size, modification time);
- the configuration (images, BQSR, reference, padding, clinical depth);
- the panel.

## 4. Germline pipeline (src/pipeline)

`commands.py` builds the commands, `executor.py` runs them with `bash -c` and `pipefail`, and
`germline.py` chains them.

| Step | CPU (GATK4) | GPU (Parabricks) |
|---|---|---|
| Alignment | `bwa mem -K 1e8 -Y` → `samtools sort` (streamed) | `pbrun fq2bam` (+ duplicates + BQSR table) |
| Duplicates | `gatk MarkDuplicates` | included in fq2bam |
| BQSR | `BaseRecalibrator` + `ApplyBQSR` (known_indels, Mills, dbSNP if present) | fq2bam table, applied by haplotypecaller |
| Calling | `HaplotypeCaller -L panel.bed` | `pbrun haplotypecaller --interval-file panel.bed` |
| Post-processing | `LeftAlignAndTrimVariants --split-multi-allelics`, GATK hard filters for SNVs / indels separately | same |

Each step writes a checkpoint, `.checkpoints/<step>.json`, holding the command fingerprint, the
chained input fingerprint and the output sizes. Relaunching a job resumes at the first invalid step.

### Alignment QC (src/pipeline/alignment_qc.py)

In FASTQ mode, after variant calling: `samtools flagstat` (mapping rate), Picard metrics
(duplicates) and depth (`samtools depth -Q20 -q20`) at every ClinVar P/LP site ≤ 50 bp of the
germline genes (site depth = minimum over its bases). Artifact `alignment_qc.json`.

## 5. Scientific domain (src/genomics)

- `panel.py`: genes, germline or somatic role, penetrance, BED export.
- `vcf_io.py`: pure-Python reader. Splits multi-allelic sites and reads per-allele fields
  (`Number=A/R`). Filters by region while reading, and reads CSQ/ANN according to the header.
- `clinvar.py`: CLNSIG classification and review stars. The ClinVar index is restricted to the panel
  and cached; annotation embedded in the VCF is the fallback. With no source at all,
  `AnnotationUnavailable` is raised.
- `qc.py`: clinical thresholds (`CLINICAL_*`), zygosity-aware VAF check.
- `analysis.py`: crosses variants, annotation and QC; every panel variant receives a category.
  Results are sorted by position.
- `risk.py`: `germlineiq-rules-v1.1` rules and analysis limitations. In FASTQ mode, coverage of known
  pathogenic sites enters the conclusion: no P/LP and < 90 % of sites covered → INDETERMINATE; a gene
  below 95 % is named "not excluded". HIGH/MODERATE are never downgraded.
- `statistics.py`: deterministic statistics (`germlineiq-stats-v1`): the whole VCF (streamed), then
  the panel variants (benign included): Ti/Tv, het/hom, fixed-bin QUAL/DP/GQ/VAF distributions,
  type-7 quantiles, per-gene table and expert checks (expected ranges, OK/WARN/NA status).
  Artifact `vcf_statistics.json`, `statistics` field of the report.

## 6. BioGPT: verified commentary and fine-tuning (src/llm)

- `knowledge.py`: disease lexicon (coordinated lists included), curated gene–disease table
  (ClinGen/NCCN, the only acceptance reference) and `verify_text`. A sentence is rejected if it
  cites a disease not associated with the gene, another panel gene, or a claim the table cannot
  verify (comparison, number, somatic context, therapy, prognosis, negation); a deterministic
  reference sentence replaces it. ClinVar counts (≥ 2★) are supporting evidence only: CLNDN
  aggregates the conditions of every submitter.
- `corpus.py`: frozen PubMed abstracts (manifest, SHA-256), gene-centric filter, train/val/test
  split by hashing the document id.
- `finetune.py`: deterministic LoRA (`python -m src.llm.finetune corpus|train|evaluate`).
- `model_evaluator.py`: test perplexity, factual accuracy on the production prompt and on prompts
  absent from training; `rescore` re-judges stored generations. An adapter is only promoted
  (`BIOGPT_ADAPTER_PATH`) if no factual metric degrades.

### BioGPT fine-tuned on the VCF statistics (interpretation of `vcf_statistics.json`)

Five-step workflow, run by `PredictionAgent` after the rule-based risk decision:

| Step | Module | What happens |
|---|---|---|
| 1. Training | `stats_finetune.py` | Facts (values + OK/LOW/HIGH/NA status, genes, risk) sampled over normal **and** abnormal ranges with the pipeline thresholds and rules v1.1; targets = reference interpretation with paraphrases; LoRA, loss on the interpretation tokens only. Real analyses of the server = held-out `real_test`, never trained on. |
| 2. Integration | `stats_finetune.py promote`, `config.settings.biogpt_stats` | The adapter is copied to `models/biogpt-germlineiq-stats/promoted/` **only if** the gate passes on both test sets (sentence precision ≥ 0.95, topic coverage ≥ 0.90, no wrong risk level, every final answer verified). |
| 3. Prediction | `stats_model.StatsInterpreter` | `extract_facts` → `render_prompt` → greedy generation (same facts → same text). |
| 4. Verification | `stats_interpretation.verify_interpretation` | Detokenizer spacing normalised, then every sentence checked: each number equals the fact of its metric (or a documented threshold), each status word matches the computed status, the risk level equals the rules, each gene holds the stated role; negations, clinical advice and claim-free sentences are rejected. |
| 5. Final answer | `stats_interpretation.assemble_final` | Per required topic: first verified model sentence, else the reference sentence; the final text is re-verified (`final_verified`). Stored in `clinical_prediction.statistics_interpretation`. |

Without a promoted adapter, or if the model fails, the final answer is the reference text
(`source: "reference"`). The interpretation never changes the risk level.

```bash
python -m src.llm.stats_finetune all      # dataset → train → evaluate → promote (≈ 10 min on an RTX 5080)
```

## 7. Verification and benchmarks

- `scripts/expert_check.py <patients/ID/output>` recomputes the report metrics with bcftools,
  samtools and the Picard metrics, without importing the backend, and compares (exit code 1 on any
  difference).
- `Backend/benchmarks/multiagent.py`: clinical accuracy on the real 1000 Genomes cohort, GIAB
  analytical validity, reproducibility, deterministic vs Mistral router, fault injection, API
  latency. Results in `benchmarks/results/` and `$LOCAL_DATA_ROOT/benchmarks/latest.json`
  (served by `GET /api/v1/benchmarks/latest`).

## 8. Adding an agent

1. Write `src/agents/my_agent.py`: a `BaseAgent` subclass whose `execute(context)` returns
   `AgentResult.ok(**outputs)`. Raise `AgentError` for a domain failure.
2. Add the new keys to `src/core/context.py`.
3. Declare a `ToolSpec` in `src/orchestration/registry.py`, with its `requires` and `produces`
   fields. Add `gpu_phase`, `exclusive`, `critical` and `cache_inputs` as needed.
4. If it must appear in the interface, add its `ui_step` to `Frontend/lib/utils/pipeline.ts`.

The planner, the MCP server and `GET /api/v1/tools` pick it up automatically.

## 9. Tests

```
tests/unit/          domain, statistics, BioGPT verification, orchestration (fake tools), pipeline (fake executor), storage
tests/integration/   full API, real agents and engine, end to end on the test VCFs
tests/fixtures/      synthetic VCFs and a mini ClinVar-format database (no real data)
```
