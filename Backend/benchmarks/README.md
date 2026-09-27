# GermlineIQ benchmarks

Two families of tools, both black-box clients of the HTTP API (start the backend first).

## 1. Multi-agent system benchmark — `multiagent.py`

The main evaluation of the platform, on real open-access data.

| Suite | What it measures | Data |
|---|---|---|
| `clinical` | Carrier sensitivity, control specificity, variant recall, unexpected confirmed P/LP calls, risk-level accuracy (with 95 % Wilson intervals), per-agent and end-to-end times | Real 1000 Genomes cohort (30× reads over the panel genes), ground truth from `scripts/find_1000g_carriers.py` |
| `analytical` | Precision / recall / F1 of the variant calls (PASS and all calls) | GIAB HG001 v4.2.1 truth set, NA12878 exome, panel regions covered ≥ 15× |
| `reproducibility` | Identical clinical content and statistics across repeated runs | Synthetic BRCA VCF + one cohort FASTQ |
| `orchestration` | Deterministic vs Mistral router: same plan, same clinical report, orchestration overhead | In-process engine, synthetic BRCA VCF |
| `robustness` | Fault injection (invalid id, path traversal, missing file, identical R1/R2, S3 URI, malformed VCF…) | API |
| `latency` | p50 / p95 / p99 of the read endpoints | API |

```bash
cd Backend
python -m benchmarks.multiagent --suites all                    # everything (~20 min on 6 CPUs)
python -m benchmarks.multiagent --suites robustness,latency     # fast suites only
```

Prerequisites: the demonstration cohort (`scripts/prepare_1000g_cohort.py`), the hg38 reference and
ClinVar; for `analytical`, the NA12878 exome processed through the FASTQ pipeline and the GIAB truth
set in `$LOCAL_DATA_ROOT/reference/giab/`.

Outputs:
- `benchmarks/results/multiagent_<timestamp>.json` and `.md` (one per run);
- `$LOCAL_DATA_ROOT/benchmarks/latest.json` / `latest.md` — merged with the previous results so that
  running a single suite keeps the others; served by `GET /api/v1/benchmarks/latest` and displayed in
  the **Benchmarks** page of the interface.

Clinical content is compared through the SHA-256 of `genomic_findings`, `clinical_prediction`,
`statistics` and `reproducibility` (timestamps, paths, hardware, timings and router name excluded).

## 2. API latency and load tools

```
benchmarks/
├── config.py              Configuration (SLA, iterations, URL)
├── metrics.py             p50/p75/p90/p95/p99 computation
├── latency_benchmark.py   Sequential benchmark per endpoint
├── load_test.py           Concurrent load test
├── pipeline_benchmark.py  End-to-end VCF submission (superseded by multiagent.py)
├── reporter.py            JSON / CSV / HTML (Plotly) reports
├── run_all.py             Single entry point
├── conftest.py            pytest fixtures
└── test_latency_sla.py    SLA tests for CI
```

```bash
pip install -r benchmarks/requirements-benchmarks.txt
python -m benchmarks.run_all                          # all latency/load suites
python -m benchmarks.run_all --suite latency
python -m benchmarks.load_test --workers 20 --levels 1 5 10 20
pytest benchmarks/test_latency_sla.py -v
```

| Variable | Default | Description |
|---|---|---|
| `BENCH_BASE_URL` | `http://localhost:8000` | Target server URL |
| `BENCH_ITERATIONS` | `30` | Requests per endpoint |
| `BENCH_WARMUP` | `3` | Warm-up requests |
| `BENCH_WORKERS` | `10` | Concurrent workers (load test) |
| `BENCH_TIMEOUT` | `30.0` | Request timeout (seconds) |
| `BENCH_SLA_P50` / `P95` / `P99` | `200` / `500` / `1000` | Latency SLAs (ms) |
| `BENCH_SLA_ERROR_RATE` | `1.0` | Maximum error rate (%) |

The `/api/v1/assistant/chat` endpoint calls Mistral with `OLLAMA_KEEP_ALIVE=0` (the model is unloaded
after each request to protect VRAM), so its latency SLA is expected to fail by design.
