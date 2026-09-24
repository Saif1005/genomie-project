# Architecture du backend ZAYNB

Document destiné aux développeurs qui reprennent ou étendent le backend.

## 1. Couches et règles de dépendance

```
app/                  API HTTP : validation des entrées, jobs, routeurs
  └─▶ src/orchestration   moteur multi-agent (registre, planificateur, routeur, cache)
        └─▶ src/agents     un agent = un outil ; lit/écrit le contexte
              ├─▶ src/genomics   domaine scientifique pur (aucune E/S réseau, aucun LLM)
              ├─▶ src/pipeline   commandes GATK/Parabricks, exécution, reprise
              ├─▶ src/llm        BioGPT (commentaire), Ollama
              └─▶ src/report     assemblage du rapport
src/core     contrats partagés (BaseAgent, clés du contexte) — dépend de rien
config/settings.py   seule source de configuration (variables d'environnement)
```

- Une couche n'importe que les couches **en dessous** d'elle.
- `src/genomics` ne dépend que de lui-même et de `config`, et reste testable sans Docker, sans GPU
  et sans réseau.
- Les dépendances lourdes (torch, transformers) sont importées **dans les fonctions** qui s'en
  servent : l'API démarre sans elles.

## 2. Contexte partagé

Les agents communiquent par un dictionnaire JSON-sérialisable dont les clés sont définies dans
`src/core/context.py`. Un agent lit ses entrées et renvoie **uniquement** ses sorties dans
`AgentResult.data` ; le moteur fusionne.

| Clé | Produite par | Contenu |
|---|---|---|
| `patient_id`, `fastq_r1`, `fastq_r2`, `vcf_uri`, `train_llm` | utilisateur | entrées |
| `fastq_r1_uri`, `fastq_r2_uri` | data_manager | FASTQ rangés dans `patients/<ID>/input` |
| `vcf_uri`, `bam_uri`, `pipeline_backend` | genomic_pipeline | VCF filtré, BAM, moteur utilisé |
| `annotated_variants_path`, `annotation`, `input_sha256` | variant_annotation | artefact JSON, version ClinVar |
| `panel_analysis`, `vcf_metrics` | vcf_analysis | variants confirmés / à confirmer / VUS |
| `risk_assessment`, `prediction_results` | prediction | niveau, justification, commentaire |
| `clinical_report`, `report_uri` | report | rapport final |

Chaque agent écrit aussi un **artefact** dans `patients/<ID>/output/` (JSON trié, donc comparable
avec `diff`), ce qui permet d'auditer ou de rejouer une étape.

## 3. Orchestration (src/orchestration)

**Registre (`registry.py`)** : chaque outil déclare `requires`, `produces`, sa phase GPU, s'il est
exclusif, s'il est critique et les entrées qui identifient un résultat réutilisable. C'est la
source unique pour le planificateur, le serveur MCP (`tools/list`) et `GET /api/v1/tools`.

**Planificateur (`planner.py`)** : chaînage arrière depuis les objectifs (`clinical_report`, plus
`training_data_path` si `train_llm`). Les données déjà présentes ne sont pas recalculées :

| Contexte fourni | Plan |
|---|---|
| FASTQ | data_manager → genomic_pipeline → variant_annotation → vcf_analysis → prediction → report |
| VCF | variant_annotation → vcf_analysis → prediction → report |
| VCF + `train_llm` | … → report → llm_training |

**Moteur (`engine.py`)** : graphe LangGraph à deux nœuds, `plan` puis `execute`, en boucle.
- À chaque tour, le plan est **recalculé** depuis le contexte courant.
- Si plusieurs outils non exclusifs sont prêts, ils tournent en parallèle, et leurs sorties sont
  fusionnées dans l'ordre du registre.
- Les outils exclusifs (GPU) tournent seuls ; le routeur choisit parmi les candidats.
- L'échec d'un outil critique arrête le plan. Celui d'un outil non critique (`llm_training`) est
  journalisé et son objectif abandonné.
- Les hooks GPU libèrent la VRAM avant Parabricks et avant BioGPT (`GPUManager`).

**Routeur (`router.py`)** : ordre du registre par défaut. Avec `ORCHESTRATOR_DETERMINISTIC=false`,
Mistral choisit parmi les outils prêts. Toute réponse invalide revient à l'ordre du registre. Le
choix ne peut pas modifier le résultat clinique, car les outils candidats sont indépendants entre
eux.

**Cache (`cache.py`)** : l'appel de variants est réutilisé si ces éléments n'ont pas changé :
- les FASTQ (chemin, taille, date de modification) ;
- la configuration (images, BQSR, référence, marge) ;
- le panel.

## 4. Pipeline germinal (src/pipeline)

`commands.py` construit les commandes, `executor.py` les exécute avec `bash -c` et `pipefail`,
et `germline.py` les enchaîne.

| Étape | CPU (GATK4) | GPU (Parabricks) |
|---|---|---|
| Alignement | `bwa mem -K 1e8 -Y` → `samtools sort` (flux) | `pbrun fq2bam` (+ duplicats + table BQSR) |
| Duplicats | `gatk MarkDuplicates` | inclus dans fq2bam |
| BQSR | `BaseRecalibrator` + `ApplyBQSR` (known_indels, Mills, dbSNP si présents) | table de fq2bam, appliquée par haplotypecaller |
| Appel | `HaplotypeCaller -L panel.bed` | `pbrun haplotypecaller --interval-file panel.bed` |
| Post-traitement | `LeftAlignAndTrimVariants --split-multi-allelics`, filtres « hard » GATK séparés SNV / indels | idem |

Chaque étape écrit un point de contrôle, `.checkpoints/<étape>.json`. Il contient l'empreinte de
la commande, l'empreinte chaînée des entrées et la taille des sorties. Relancer un job reprend à
la première étape invalide.

## 5. Domaine scientifique (src/genomics)

- `panel.py` : gènes, rôle germinal ou somatique, pénétrance, export BED.
- `vcf_io.py` : lecture en Python pur. Il éclate les sites multi-alléliques et lit les champs par
  allèle (`Number=A/R`). Il filtre par région dès la lecture, et lit CSQ/ANN selon l'en-tête.
- `clinvar.py` : classification CLNSIG et étoiles de revue. L'index ClinVar est restreint au panel
  et mis en cache ; l'annotation embarquée dans le VCF sert de secours. Sans aucune source,
  `AnnotationUnavailable` est levée.
- `qc.py` : seuils cliniques (`CLINICAL_*`), contrôle de la VAF selon la zygotie.
- `analysis.py` : croise variants, annotation et QC. Résultats triés par position.
- `risk.py` : règles `zaynb-rules-v1` et limites de l'analyse.

## 6. Ajouter un agent

1. Écrire `src/agents/mon_agent.py` : une sous-classe de `BaseAgent` dont `execute(context)`
   renvoie `AgentResult.ok(**sorties)`. Lever `AgentError` pour un échec métier.
2. Ajouter les nouvelles clés dans `src/core/context.py`.
3. Déclarer un `ToolSpec` dans `src/orchestration/registry.py`, avec ses champs `requires` et
   `produces`. Selon le besoin, ajouter `gpu_phase`, `exclusive`, `critical` et `cache_inputs`.
4. S'il doit apparaître dans l'interface, ajouter l'étape `ui_step` dans
   `Frontend/lib/utils/pipeline.ts`.

Le planificateur, le serveur MCP et `GET /api/v1/tools` le prennent en compte automatiquement.

## 7. Tests

```
tests/unit/          domaine, orchestration (outils factices), pipeline (exécuteur factice), stockage
tests/integration/   API complète, agents et moteur réels, de bout en bout sur les VCF de test
tests/fixtures/      VCF synthétiques et mini-base au format ClinVar (aucune donnée réelle)
```
