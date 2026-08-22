# Benchmarks — Zaynb Genomic Backend

> **Projet de doctorat** — Mesure de latence, throughput et fiabilite du pipeline genomique
> (GATK Parabricks + LangGraph + MCP + BioGPT)

---

## Structure du dossier

```
benchmarks/
├── __init__.py                  # Package Python
├── config.py                    # Configuration centralisee (SLA, iterations, URL)
├── metrics.py                   # Calcul des percentiles et statistiques
├── latency_benchmark.py         # Benchmark sequentiel (endpoint par endpoint)
├── load_test.py                 # Test de charge concurrent (N workers)
├── pipeline_benchmark.py        # Benchmark bout-en-bout (submit → poll → result)
├── reporter.py                  # Generation de rapports JSON / CSV / HTML
├── run_all.py                   # Point d'entree unique
├── conftest.py                  # Fixtures pytest
├── test_latency_sla.py          # Tests pytest integres en CI/CD
├── requirements-benchmarks.txt  # Dependances
└── results/                     # Rapports generes (gitignore)
```

---

## Installation rapide

```powershell
# Depuis le dossier Backend/
pip install -r benchmarks/requirements-benchmarks.txt
```

---

## Utilisation

### 1. Demarrer le serveur FastAPI

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Lancer tous les benchmarks

```powershell
# Suite complete (latence + charge + pipeline)
python -m benchmarks.run_all

# Seulement la latence
python -m benchmarks.run_all --suite latency

# Contre un serveur distant
python -m benchmarks.run_all --url http://ec2-xxx.amazonaws.com:8000 --suite all
```

### 3. Benchmarks individuels

```powershell
# Latence sequentielle (30 iterations + 3 warm-up par defaut)
python -m benchmarks.latency_benchmark --iterations 50 --warmup 5

# Test de charge concurrent
python -m benchmarks.load_test --levels 1 5 10 20 50

# Pipeline complet bout-en-bout
python -m benchmarks.pipeline_benchmark --runs 10
```

### 4. Tests pytest avec verification SLA

```powershell
# Lance les tests de latence avec assertion SLA
pytest benchmarks/test_latency_sla.py -v

# Avec rapport de couverture
pytest benchmarks/test_latency_sla.py -v --tb=short
```

---

## Variables d'environnement

| Variable               | Valeur defaut             | Description                          |
|------------------------|---------------------------|--------------------------------------|
| `BENCH_BASE_URL`       | `http://localhost:8000`   | URL du serveur cible                 |
| `BENCH_ITERATIONS`     | `30`                      | Nombre d'iterations par endpoint     |
| `BENCH_WARMUP`         | `3`                       | Requetes de warm-up ignorees         |
| `BENCH_WORKERS`        | `10`                      | Workers concurrents (load test)      |
| `BENCH_TIMEOUT`        | `30.0`                    | Timeout requete (secondes)           |
| `BENCH_SLA_P50`        | `200`                     | SLA p50 en ms                        |
| `BENCH_SLA_P95`        | `500`                     | SLA p95 en ms                        |
| `BENCH_SLA_P99`        | `1000`                    | SLA p99 en ms                        |
| `BENCH_SLA_ERROR_RATE` | `1.0`                     | Taux d'erreur max acceptable (%)     |
| `BENCH_RESULTS_DIR`    | `benchmarks/results`      | Dossier de sortie des rapports       |

---

## Endpoints mesures

| Endpoint                          | Type          | Description                          |
|-----------------------------------|---------------|--------------------------------------|
| `GET /health`                     | Latence pure  | Health check — baseline              |
| `POST /api/v1/analyze`            | Soumission    | Pipeline FASTQ (GATK Parabricks)     |
| `POST /api/v1/analyze/vcf`        | Soumission    | Workflow VCF direct                  |
| `POST /api/v1/assistant/chat`     | LLM           | Chat assistant IA (BioGPT/Mistral)   |
| `GET /api/v1/jobs/{id}`           | Polling       | Statut du job                        |
| `GET /api/v1/jobs/{id}/report`    | Resultat      | Rapport clinique final               |

---

## Rapports generes

Chaque execution genere 3 fichiers dans `benchmarks/results/` :

```
bench_latency_20260822T163000Z.json   ← donnees brutes (import R / pandas)
bench_latency_20260822T163000Z.csv    ← tableau (Excel / LaTeX pgfplots)
bench_latency_20260822T163000Z.html   ← rapport interactif (graphiques Plotly)
```

### Metriques calculees

- **Min, Moyenne, Ecart-type**
- **p50 (median), p75, p90, p95, p99, Max**
- **Throughput** (req/s effectif)
- **Taux d'erreur** (%)
- **Verification SLA** automatique (✅ / ❌)

---

## SLA par defaut (pipeline genomique)

| Percentile | Seuil | Justification                                    |
|------------|-------|--------------------------------------------------|
| p50        | 200ms | Latence acceptable pour soumission de job        |
| p95        | 500ms | 95% des requetes sous 500ms (API responsive)     |
| p99        | 1000ms| Queue longue toleree (pipeline GPU en arriere-plan) |
| Erreurs    | 1%    | Taux d'erreur max acceptable en production       |

> Ces seuils s'appliquent a la **couche API** (soumission du job),
> pas au pipeline genomique complet (GATK Parabricks peut prendre 10-60 minutes).

---

## Integration CI/CD

```yaml
# .github/workflows/benchmark.yml
- name: Run latency benchmarks
  run: |
    pip install -r benchmarks/requirements-benchmarks.txt
    pytest benchmarks/test_latency_sla.py -v --tb=short
  env:
    BENCH_BASE_URL: http://localhost:8000
    BENCH_ITERATIONS: 20
```

---

## Interpretation des resultats (doctorat)

### Lecture des percentiles

```
p50 = 45ms  → la moitie des requetes repondent en moins de 45ms
p95 = 180ms → 95% des requetes repondent en moins de 180ms
p99 = 450ms → 99% des requetes repondent en moins de 450ms
Max = 1200ms → les outliers (timeouts, GC pauses, etc.)
```

### Loi de scalabilite d'Amdahl

Le `load_test.py` mesure la degradation de latence en fonction du nombre
de workers concurrents. Les resultats permettent de tracer la courbe
de scalabilite et d'estimer la portion sequentielle du systeme.

### Recommendations

- **Warm-up obligatoire** : les premieres requetes sont plus lentes (JIT, connexion TCP)
- **30+ iterations** pour des percentiles stables
- **Tester sur le meme reseau** que la production pour eviter les biais
- **Repeter 3x** et prendre la mediane des p95 pour les publications
