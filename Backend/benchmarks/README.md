# Benchmarks — Zaynb Genomic Backend

> Projet de Doctorat — Mesure de latence, throughput et fiabilite de l'API genomique

## Structure

```
benchmarks/
├── __init__.py                  # Package Python
├── config.py                    # Configuration (SLA, iterations, URL)
├── metrics.py                   # Calcul p50/p75/p90/p95/p99
├── latency_benchmark.py         # Benchmark sequentiel par endpoint
├── load_test.py                 # Test de charge concurrent
├── pipeline_benchmark.py        # Benchmark bout-en-bout (submit->poll->result)
├── reporter.py                  # Rapports JSON / CSV / HTML (Plotly)
├── run_all.py                   # Point d'entree unique
├── conftest.py                  # Fixtures pytest
├── test_latency_sla.py          # Tests SLA integres CI/CD
├── requirements-benchmarks.txt  # Dependances
└── results/                     # Rapports generes (gitignored)
```

## Installation

```bash
pip install -r benchmarks/requirements-benchmarks.txt
```

## Utilisation

```bash
# Demarrer le serveur FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Tous les benchmarks
python -m benchmarks.run_all

# Seulement la latence
python -m benchmarks.run_all --suite latency

# Test de charge
python -m benchmarks.run_all --suite load

# Pipeline complet
python -m benchmarks.run_all --suite pipeline

# Serveur distant
python -m benchmarks.run_all --url http://ec2-xxx:8000 --suite all

# Tests pytest avec SLA
pytest benchmarks/test_latency_sla.py -v
```

## Variables d'environnement

| Variable               | Defaut                  | Description                      |
|------------------------|-------------------------|----------------------------------|
| BENCH_BASE_URL         | http://localhost:8000   | URL du serveur cible             |
| BENCH_ITERATIONS       | 30                      | Iterations par endpoint          |
| BENCH_WARMUP           | 3                       | Requetes de warm-up              |
| BENCH_WORKERS          | 10                      | Workers concurrents (load test)  |
| BENCH_TIMEOUT          | 30.0                    | Timeout requete (secondes)       |
| BENCH_SLA_P50          | 200                     | SLA p50 en ms                    |
| BENCH_SLA_P95          | 500                     | SLA p95 en ms                    |
| BENCH_SLA_P99          | 1000                    | SLA p99 en ms                    |
| BENCH_SLA_ERROR_RATE   | 1.0                     | Taux d'erreur max (%)            |
| BENCH_RESULTS_DIR      | benchmarks/results      | Dossier de sortie                |

## Endpoints mesures

| Endpoint                       | Description                       |
|--------------------------------|-----------------------------------|
| GET /health                    | Health check (baseline)           |
| POST /api/v1/analyze           | Pipeline FASTQ (GATK Parabricks)  |
| POST /api/v1/analyze/vcf       | Workflow VCF direct               |
| POST /api/v1/assistant/chat    | Chat assistant IA (BioGPT)        |
| GET /api/v1/jobs/{id}          | Statut du job                     |

## Rapports generes

```
results/bench_latency_20260822T163000Z.json   <- donnees brutes (R/pandas)
results/bench_latency_20260822T163000Z.csv    <- tableau (Excel/LaTeX)
results/bench_latency_20260822T163000Z.html   <- graphiques interactifs
```

## SLA par defaut

| Percentile | Seuil  | Justification                              |
|------------|--------|--------------------------------------------|
| p50        | 200ms  | Latence soumission de job                  |
| p95        | 500ms  | 95% des requetes sous 500ms (API reactive) |
| p99        | 1000ms | Queue longue toleree (pipeline GPU)        |
| Erreurs    | 1%     | Taux d'erreur max en production            |
