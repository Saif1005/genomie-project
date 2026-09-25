# Plateforme génomique clinique multi-agents

**Zonal Analysis for Yielding Next-generation Biomarkers**

Plateforme de recherche qui transforme des données de séquençage (FASTQ ou VCF) en **rapport de
risque héréditaire de cancer du sein**, sur un **serveur local** (aucun service cloud), avec une
orchestration multi-agent LangGraph, un pipeline GATK accéléré par GPU (Parabricks) et une
interface web clinique.

> **Avertissement** — Prototype de recherche. Les résultats ne constituent pas un diagnostic
> médical et doivent être validés par un généticien clinique ou un oncologue.

Installation et exploitation : **[DEPLOY_LOCAL.md](DEPLOY_LOCAL.md)** · Architecture détaillée :
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

---

## Ce que fait le système

| Entrée | Chaîne d'agents | Sortie |
|---|---|---|
| **FASTQ** R1 + R2 | préparation → appel de variants (Parabricks GPU ou GATK4 CPU) → annotation ClinVar → contrôle qualité et classification → risque → rapport | Rapport clinique JSON |
| **VCF** | annotation ClinVar → contrôle qualité et classification → risque → rapport | Rapport clinique JSON |

Le plan est calculé dynamiquement par l'orchestrateur à partir des données fournies (un VCF saute
l'alignement ; un appel de variants déjà fait sur les mêmes FASTQ est réutilisé).

### Principes

- **Déterministe** : le niveau de risque est décidé par des règles explicites et versionnées
  (`zaynb-rules-v1`), jamais par un modèle de langage. Même VCF + même panel + même version ClinVar
  → même rapport, avec l'empreinte SHA-256 de l'entrée et les versions dans le rapport.
- **Prudent** : sans annotation ClinVar, l'analyse refuse de conclure ; un variant pathogène de
  qualité insuffisante donne un risque **INDÉTERMINÉ** (confirmation requise), jamais « faible ».
- **Données publiques** : hg38 et sites connus (GATK Resource Bundle, Broad Institute), ClinVar
  (NCBI, domaine public), coordonnées des gènes (Ensembl / NCBI Gene).
- **Local** : données de santé sur le disque du serveur, ports liés à `127.0.0.1` par défaut,
  accès via AnyDesk.

---

## Panel héréditaire cancer du sein

| Pénétrance | Gènes | Risque si variant pathogène confirmé |
|---|---|---|
| Élevée | BRCA1, BRCA2, PALB2, TP53, PTEN, CDH1, STK11 | ÉLEVÉ |
| Modérée | CHEK2, ATM, BARD1, RAD51C, RAD51D, NF1 | MODÉRÉ |
| Somatique (hors risque germinal) | PIK3CA, ERBB2/HER2, MYC | — |

Coordonnées GRCh38 et rôles : `Backend/data/cancer_genes/cancer_genes_db.json`.

| Niveau | Règle |
|---|---|
| HIGH | ≥ 1 variant P/LP (ClinVar) confirmé dans un gène à haute pénétrance |
| MODERATE | ≥ 1 variant P/LP confirmé dans un gène à pénétrance modérée, ou allèle « low penetrance » |
| INDETERMINATE | aucun confirmé, mais un variant à confirmer (QUAL < 30, DP < 15, VAF hors 0,25–0,75, filtre GATK, perte de fonction non classée) |
| LOW | aucun variant P/LP ni à confirmer sur les 13 gènes germinaux |

Limites (rappelées dans chaque rapport) : grands réarrangements / CNV non détectés, couverture du
panel non mesurée à partir du VCF, classification dépendante de ClinVar.

---

## Architecture

```
Frontend Next.js (:3000) ──/api/v1 (proxy)──▶ Backend FastAPI (:8000)
                                                 │
                                   Orchestrateur LangGraph : planifier ⇄ exécuter
                                   registre d'outils · planificateur · routeur · cache
                                                 │
   DataManager → VariantCalling → VariantAnnotation → VCFAnalysis → Prediction → Report
                  (Parabricks/GATK4)   (ClinVar)        (QC)        (règles + BioGPT)
                                                 │
                         /data/zaynb : référence, ClinVar, patients, modèles
                         Ollama (Mistral, assistant/routeur) · conteneurs GATK/Parabricks
```

| Couche | Technologies |
|---|---|
| Interface | Next.js 14, TypeScript, Tailwind CSS |
| API | FastAPI, Pydantic v2 |
| Orchestration | LangGraph, serveur MCP (stdio) |
| Génomique | BWA-MEM, GATK 4.2, NVIDIA Parabricks 4.6 |
| Interprétation | Règles déterministes + ClinVar ; BioGPT (commentaire non décisionnel) |
| Assistant | Mistral v0.3 via Ollama |
| Déploiement | Docker Compose, serveur local |

### Structure du dépôt

```
Backend/
├── app/                  API : main.py, schemas.py, jobs.py (file + état persisté), routers/
├── config/settings.py    Configuration unique (Backend/.env)
├── src/
│   ├── core/             Contrat des agents, vocabulaire du contexte
│   ├── orchestration/    Registre d'outils, planificateur, routeur, moteur LangGraph, cache
│   ├── agents/           Un agent = un outil (data_manager, variant_calling, annotation, …)
│   ├── genomics/         Domaine scientifique pur : panel, lecture VCF, ClinVar, QC, risque
│   ├── pipeline/         Commandes GATK/Parabricks, exécution, reprise sur incident
│   ├── llm/              BioGPT, client Ollama, préparation LoRA
│   ├── report/, schemas/ Rapport clinique
│   ├── mcp/              Serveur MCP
│   ├── storage/          Stockage patient (LOCAL_DATA_ROOT)
│   └── utils/            GPU/VRAM, validation des entrées
├── data/cancer_genes/    Panel de gènes
└── tests/                unit/, integration/, fixtures/ (VCF synthétiques)
Frontend/                 Interface clinique (app/, components/, lib/, types/)
scripts/                  start, stop, logs, check_prereqs, download_reference, pull_models, smoke_test
docker-compose.local.yml  Stack locale (+ docker-compose.gpu.yml si GPU)
```

---

## Démarrage rapide

```bash
bash scripts/check_prereqs.sh                  # diagnostic du serveur
bash scripts/start.sh                           # construit et démarre la stack
bash scripts/download_reference.sh --clinvar-only   # ClinVar (~0,2 Go) : suffit pour les VCF
bash scripts/smoke_test.sh                      # vérification de bout en bout
```

Puis ouvrir **http://localhost:3000** dans le navigateur du serveur. Pour le mode FASTQ :
`bash scripts/download_reference.sh` (hg38, ~9,7 Go) et `bash scripts/pull_models.sh`.

Tests backend : `cd Backend && pip install -r requirements-dev.txt && python -m pytest`.

### API REST

| Méthode | Endpoint | Description |
|---|---|---|
| GET | `/health` | Santé, moteur GPU/CPU choisi, panel, disponibilité ClinVar |
| GET | `/api/v1/tools` | Agents disponibles (entrées / sorties) |
| POST | `/api/v1/analyze` | Analyse FASTQ (`fastq_r1`, `fastq_r2` : chemins serveur) |
| POST | `/api/v1/analyze/vcf` | Analyse VCF (`vcf_path`) |
| POST | `/api/v1/analyze/upload` | Upload FASTQ puis analyse |
| GET | `/api/v1/jobs/{id}` | Statut, plan et étapes |
| GET | `/api/v1/jobs/{id}/report` | Rapport clinique JSON |
| POST | `/api/v1/assistant/chat` | Assistant conversationnel |

---

## Résultats

Mesures **historiques** du prototype sur AWS g4dn (NVIDIA T4), avant la migration locale et la
refonte de l'interprétation (voir `Rapport_ZAYNB_Benchmark_Visuel.pdf`) :

| Métrique | CPU (GATK4) | GPU (Parabricks) |
|---|---|---|
| Pipeline complet (échantillon ≈ 6 Gb) | ~5 h | ~22 min |
| Alignement BWA-MEM | ~45 min | ~4,5 min |

Ces temps portaient sur un appel de variants génome entier ; HaplotypeCaller est désormais
restreint au panel. Les métriques de justesse (F1, concordance) ne sont pas reproductibles depuis
le dépôt et doivent être remesurées : hap.py sur GIAB HG002 restreint au panel, plus des contrôles
positifs BRCA connus. Exemple de variant de référence : BRCA1 c.5266dupC (rs80357906),
**chr17:43057062 T>TG** en GRCh38.

---

## Perspectives

- Validation analytique (GIAB/hap.py) et clinique (contrôles positifs) scriptée dans le dépôt
- Détection des CNV (grands réarrangements BRCA1/2)
- Mesure de couverture du panel à partir du BAM (régions insuffisamment couvertes)
- Intégration FHIR / DMP hospitalier ; démarche réglementaire (IVDR)

---

## Licence et usage

Projet de recherche académique — validation doctorale en informatique et bioinformatique.
**Ne pas utiliser en production clinique sans validation médicale et réglementaire.**

Dépôt : [Saif1005/genomie-project](https://github.com/Saif1005/genomie-project)
