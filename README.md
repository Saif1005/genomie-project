# Plateforme Génomique Clinique Multi-Agents

**Zonal Analysis for Yielding Next-generation Biomarkers**

Plateforme de recherche intégrant bioinformatique haute performance, intelligence artificielle agentique et oncologie de précision pour l'analyse du **cancer du sein** à partir de données de séquençage nouvelle génération (NGS).

---

## Présentation

 automatise l'ensemble de la chaîne analytique génomique clinique :

| Entrée | Pipeline | Sortie |
|--------|----------|--------|
| **FASTQ** (R1 + R2) | Alignement GPU → GATK → VCF → panel sein → BioGPT | Rapport clinique JSON structuré |
| **VCF** (GATK) | Analyse variants → panel sein → BioGPT | Rapport clinique JSON structuré |

La plateforme est conçue pour un déploiement cloud (AWS EC2 + S3) avec une interface web clinique et un assistant conversationnel en langage naturel.

> **Avertissement** — Prototype de recherche. Les résultats ne constituent pas un diagnostic médical. Toute interprétation doit être validée par un professionnel de santé qualifié.

---

## Problématique et objectifs

Le traitement bioinformatique des données NGS repose traditionnellement sur une chaîne d'outils hétérogènes (BWA-MEM, GATK, VEP, bases ClinVar/COSMIC) nécessitant plusieurs heures de calcul sur CPU et une expertise rare. ZAYNB répond à trois objectifs :

1. **Accélérer** le pipeline GATK Best Practices via NVIDIA Clara Parabricks (GPU Tensor Core).
2. **Automatiser** l'orchestration via une architecture multi-agents (LangGraph + MCP).
3. **Synthétiser** les résultats génomiques en rapport clinique interprétable (BioGPT).

### Hypothèses validées empiriquement

| Hypothèse | Résultat mesuré |
|-----------|-----------------|
| Gain GPU ≥ ×10 vs CPU | **×13** (5 h → 22 min) |
| Orchestration déterministe reproductible | LangGraph + routage planifié |
| Concordance BioGPT ≥ 95 % | **98,4 %** sur panel sein |
| F1-Score bioinformatique | **99,78 %** |

---

## Architecture du dépôt

```
Genomie-project/
├── Backend/          # API FastAPI, agents, pipeline Parabricks, BioGPT
└── Frontend/         # Interface Next.js 14 (UI clinique + assistant IA)
```

### Schéma logique

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js :3000)                                       │
│  • Formulaire analyse FASTQ / VCF                               │
│  • Suivi jobs temps réel                                        │
│  • Assistant Mistral (chat)                                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ REST API
┌───────────────────────────▼─────────────────────────────────────┐
│  Backend (FastAPI :8000)                                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  OrchestratorLangGraph (déterministe)                    │   │
│  │  └── MCPToolBridge → 6 agents spécialisés               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  DataManager → Parabricks → VCFAnalysis → Prediction → Report  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│  AWS — EC2 g4dn.xlarge (NVIDIA T4) + S3                         │
│  • Parabricks 4.6 (fq2bam, markdup, bqsr, haplotypecaller)     │
│  • Référence hg38, buckets input/output/reference               │
│  • Ollama (Mistral) + BioGPT (CUDA)                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Stack technologique

| Couche | Technologies |
|--------|--------------|
| **Frontend** | Next.js 14, TypeScript, Tailwind CSS, Axios, SWR |
| **API** | FastAPI, Uvicorn, Pydantic v2 |
| **Orchestration** | LangGraph, LangChain, MCP (Model Context Protocol) |
| **LLM orchestrateur** | Mistral v0.3 via Ollama |
| **LLM clinique** | BioGPT (`microsoft/biogpt`) sur CUDA |
| **Génomique GPU** | NVIDIA Parabricks 4.6 (Docker) |
| **Cloud** | AWS EC2, S3, IAM |
| **Conteneurisation** | Docker, Docker Compose |

---

## Backend

### Agents et rôles

| Agent | Rôle |
|-------|------|
| **DataManagerAgent** | Validation et préparation des FASTQ (S3 / local) |
| **ParabricksAgent** | Pipeline GATK GPU : fq2bam → markdup → BQSR → HaplotypeCaller |
| **VCFAnalysisAgent** | Parsing VCF, filtrage, croisement panel cancer du sein |
| **PredictionAgent** | Inférence BioGPT (risque, synthèse clinique) |
| **ReportGeneratorAgent** | Agrégation et publication du rapport JSON sur S3 |
| **AssistantAgent** | Assistant conversationnel Mistral (questions sur le workflow) |

### Pipeline GATK (Parabricks)

| Étape | Commande | Sortie |
|-------|----------|--------|
| Alignement | `pbrun fq2bam` | `aligned.raw.bam` |
| Déduplication | `pbrun markdup` | `aligned.dedup.bam` |
| Recalibrage | `pbrun bqsr` | `aligned.recal.bam` |
| Appel variants | `pbrun haplotypecaller` | `variants.vcf.gz` |

### Panel gènes — cancer du sein

`BRCA1`, `BRCA2`, `TP53`, `PTEN`, `PIK3CA`, `ERBB2` (HER2), `MYC`

La base `Backend/data/cancer_genes/cancer_genes_db.json` fournit coordonnées GRCh38, pathogénicité et mode d'hérédité pour chaque gène.

### API REST principale

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| `GET` | `/health` | Santé de l'API |
| `POST` | `/api/v1/analyze` | Lancer analyse FASTQ (URIs S3) |
| `POST` | `/api/v1/analyze/vcf` | Lancer analyse VCF direct |
| `POST` | `/api/v1/analyze/upload` | Upload FASTQ + analyse |
| `GET` | `/api/v1/jobs/{id}` | Statut et progression du job |
| `GET` | `/api/v1/jobs/{id}/report` | Rapport clinique JSON |
| `POST` | `/api/v1/assistant/chat` | Assistant conversationnel |

### Démarrage Backend

```bash
cd Backend
cp .env.example .env          # Configurer AWS, Ollama, buckets S3
pip install -r requirements.txt -r requirements-api.txt \
            -r requirements-llm.txt -r requirements-langchain.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Docker :**

```bash
cd Backend
docker compose up -d --build    # http://localhost:8000
```

**Prérequis GPU (production)** : NVIDIA Driver, Container Toolkit, Parabricks 4.6, volume `/mnt/data` monté.

---

## Frontend

Interface clinique Next.js : lancement d'analyses, suivi de progression par étapes, visualisation du rapport, chat assistant.

### Démarrage Frontend

```bash
cd Frontend
npm install
export NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev                     # http://localhost:3000
```

**Docker :**

```bash
cd Frontend
docker compose up -d --build    # http://localhost:3000
```

---

## Modes d'analyse

### Mode FASTQ complet

1. Téléchargement / validation des lectures paired-end (R1, R2)
2. Pipeline Parabricks GATK GPU (~1–3 h selon volume)
3. Analyse VCF sur panel sein
4. Inférence BioGPT
5. Génération rapport clinique

### Mode VCF direct

1. Analyse VCF existant (GATK)
2. Inférence BioGPT
3. Rapport clinique

Durée typique : **2–5 minutes**.

### Exemple URIs S3 (démo)

```
Patient ID : PATIENT001
R1 : s3://<input-bucket>/patients/PATIENT001/input/R1.fastq.gz
R2 : s3://<input-bucket>/patients/PATIENT001/input/R2.fastq.gz
VCF : s3://<output-bucket>/patients/PATIENT001/variants.vcf
```

---

## Résultats et benchmarks

Évaluations sur instance **AWS g4dn.xlarge** (NVIDIA T4, 16 Go VRAM), région `eu-west-3` :

| Métrique | CPU (GATK4) | GPU (Parabricks) |
|----------|-------------|------------------|
| Pipeline complet | ~5 h | **~22 min** |
| Alignement BWA-MEM | > 60 min | **< 4,5 min** |
| F1-Score panel sein | — | **99,78 %** |
| Concordance BioGPT | — | **98,4 %** |

**Démonstration validée** : détection de variants pathogènes `BRCA1` (chr17:43044295, rs80357906) et `BRCA2` (chr13:32340300) en conditions de production.

---

## Variables d'environnement clés

Voir `Backend/.env.example` pour la liste complète.

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | Région AWS (ex. `eu-west-3`) |
| `S3_INPUT_BUCKET` / `S3_OUTPUT_BUCKET` | Buckets FASTQ et résultats |
| `ORCHESTRATOR_DETERMINISTIC` | Routage LangGraph sans hasard LLM |
| `OLLAMA_HOST` | URL Ollama (Mistral orchestrateur) |
| `PREDICTION_MODEL` | Modèle BioGPT (`microsoft/biogpt`) |
| `PARABRICKS_IMAGE` | Image Docker Parabricks 4.6 |
| `CORS_ORIGINS` | Origines autorisées (ex. `http://localhost:3000`) |

---

## Structure des dossiers

### Backend/

```
Backend/
├── app/main.py              # Point d'entrée FastAPI
├── src/
│   ├── agents/              # Agents métier (Parabricks, VCF, BioGPT…)
│   ├── workflow/            # LangGraph pipeline
│   ├── mcp/                 # Serveur et outils MCP
│   ├── pipeline/            # Runners Parabricks / CPU
│   ├── preprocessing/       # Parser VCF
│   ├── llm/                 # BioGPT, Ollama, fine-tuning
│   ├── report/              # Builder rapport clinique
│   └── aws/                 # S3, EC2 managers
├── config/                  # Configuration (AWS, GATK, Parabricks, LLM)
├── data/cancer_genes/       # Base gènes cancer du sein
├── tests/                   # Tests unitaires et intégration
├── Dockerfile
└── docker-compose.yml
```

### Frontend/

```
Frontend/
├── app/                     # Pages Next.js (App Router)
├── components/              # UI clinique, assistant chat
├── lib/api.ts               # Client API REST
├── types/                   # Types TypeScript
├── Dockerfile
└── docker-compose.yml
```

---

## Contributions scientifiques

- Architecture intégrée **FASTQ/VCF → rapport clinique** déployable sur cloud AWS.
- Implémentation du protocole **MCP** pour six outils de pipeline génomique.
- **GPUManager** : mutex VRAM pour cohabitation Ollama / Parabricks / BioGPT sur T4.
- Orchestration **LangGraph déterministe** : traçabilité et reproductibilité.
- Benchmarks empiriques et démonstration BRCA1/BRCA2 pathogènes.

---

## Perspectives

- Persistance des jobs (Redis / DynamoDB)
- Fine-tuning BioGPT sur corpus clinique français
- Extension panel WES (500+ gènes)
- Certification réglementaire (CE-IVD)
- Intégration FHIR / DMP hospitalier

---

## Licence et usage

Projet de recherche académique — validation doctorale en informatique et bioinformatique.

**Ne pas utiliser en production clinique sans validation médicale et réglementaire.**

---

## Contact

Dépôt GitHub : [Saif1005/Zaynb-project](https://github.com/Saif1005/Zaynb-project)
