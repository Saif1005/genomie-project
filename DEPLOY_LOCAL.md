# ZAYNB — Déploiement sur serveur local (on-premise, accès AnyDesk)

Ce guide installe ZAYNB (backend FastAPI, frontend Next.js, Ollama/Mistral, BioGPT,
pipeline GATK/Parabricks) sur **un serveur physique**, sans aucun service cloud.
Toutes les données de référence sont publiques : hg38 et sites connus (GATK Resource Bundle,
Broad Institute), ClinVar (NCBI, domaine public).

---

## 1. Prérequis

| Élément | Minimum | Recommandé |
|---|---|---|
| OS | Linux x86_64 (Ubuntu 22.04 / 24.04 testés) | Ubuntu 22.04 LTS natif |
| CPU | 8 cœurs | ≥ 16 cœurs |
| RAM | 16 Go (mode VCF) / 32 Go (FASTQ) | ≥ 64 Go |
| Disque | 200 Go libres | ≥ 1 To SSD |
| GPU (optionnel) | — | NVIDIA ≥ 16 Go VRAM, driver ≥ 525 (Parabricks 4.6) |
| Logiciels | Docker Engine + Docker Compose v2, curl, python3 | + NVIDIA Container Toolkit si GPU |
| Réseau | Internet sortant pour l'installation (images, modèles, référence) | — |

**Sans GPU, ou avec un GPU de moins de 16 Go de VRAM**, le pipeline FASTQ bascule automatiquement sur GATK4 CPU.
L'alignement BWA-MEM prend alors plusieurs heures pour un génome, mais l'appel de variants est restreint au panel. Le mode VCF direct reste rapide (secondes).

## 2. Arborescence des données

Toutes les données vivent sous `LOCAL_DATA_ROOT` (par défaut `/data/zaynb`) :

```
/data/zaynb/
├── reference/hg38/        # FASTA, .fai, .dict, index BWA, known-sites BQSR
├── reference/clinvar/     # ClinVar GRCh38 + index du panel (cache)
├── reference/panels/      # BED du panel (généré)
├── patients/<ID>/input/   # FASTQ R1/R2, VCF déposés par l'utilisateur
├── patients/<ID>/output/  # BAM, VCF, rapports JSON
├── models/
│   ├── ollama/            # modèles Mistral (volume Ollama)
│   └── huggingface/       # cache BioGPT (HF_HOME)
└── tmp/                   # travail, cache des résultats, état des jobs (tmp/jobs)
```

Ce répertoire est monté **au même chemin** dans le conteneur backend. C'est indispensable,
car les conteneurs GATK/Parabricks sont lancés par le démon Docker de l'hôte.

## 3. Installation pas à pas

```bash
# 0. Récupérer le code (branche feature/local-deployment)
git clone <url-du-depot> zaynb && cd zaynb
git checkout feature/local-deployment

# 1. Diagnostic (lecture seule) : OK / WARN / KO + correctif suggéré
bash scripts/check_prereqs.sh

# 2. Répertoire de données
sudo mkdir -p /data/zaynb && sudo chown -R $USER: /data/zaynb

# 3. Configuration (créée automatiquement par start.sh si absente)
cp Backend/.env.local.example Backend/.env && chmod 600 Backend/.env
#    → relire : LOCAL_DATA_ROOT, PARABRICKS_MEMORY_GB (≈ RAM − 8 Go), BIND_ADDRESS

# 4. Construire et démarrer (ajoute docker-compose.gpu.yml si GPU utilisable par Docker)
bash scripts/start.sh

# 5. Modèles : Mistral (~4 Go), BioGPT (~1,6 Go), image GATK (~2 Go), Parabricks si GPU ≥ 16 Go
bash scripts/pull_models.sh

# 6. Données de référence publiques
bash scripts/download_reference.sh --clinvar-only   # ClinVar (~0,2 Go) : indispensable, suffit pour les VCF
bash scripts/download_reference.sh                  # + hg38 (≈ 9,7 Go ; ≈ 21 Go avec --with-dbsnp) pour les FASTQ

# 7. Vérification de bout en bout
bash scripts/check_prereqs.sh
bash scripts/smoke_test.sh
```

Les étapes 5 et 6 demandent une confirmation avant de télécharger (option `--yes` pour l'automatiser).
`download_reference.sh` reprend là où il s'est arrêté si on le relance, et vérifie tailles et MD5.
ClinVar est publié chaque semaine : `bash scripts/download_reference.sh --clinvar-only --refresh-clinvar`
le met à jour (la version utilisée est inscrite dans chaque rapport).

## 4. Exploitation

| Commande | Rôle |
|---|---|
| `bash scripts/start.sh [--no-build]` | Démarre ollama + backend + frontend, attend `/health` |
| `bash scripts/stop.sh` | Arrête tout (données et modèles conservés) |
| `bash scripts/logs.sh [backend\|frontend\|ollama]` | Logs en continu |
| `bash scripts/smoke_test.sh [--fastq R1 R2]` | Santé + analyse VCF synthétique BRCA1/BRCA2 → risque HIGH attendu (+ FASTQ optionnel) |
| `curl http://127.0.0.1:8000/health` | Moteur choisi (gpu/cpu) et raison, GPU détectés, panel, disponibilité ClinVar |
| `curl http://127.0.0.1:8000/api/v1/tools` | Agents de l'orchestrateur et leurs entrées/sorties |

**Lancer une analyse** : déposer les fichiers dans `/data/zaynb/patients/<ID>/input/`, puis saisir
dans l'interface le **chemin serveur** (ex. `/data/zaynb/patients/P001/input/sample.vcf`).
Tout chemin hors de `LOCAL_DATA_ROOT` est refusé. Chaque étape écrit son résultat dans
`patients/<ID>/output/` (`annotated_variants.json`, `panel_analysis.json`, rapport `REP-*.json`).
Relancer un job interrompu reprend l'appel de variants à la dernière étape terminée.

**Choix du pipeline** (`PIPELINE_BACKEND` dans `Backend/.env`) :
- `auto` (défaut) : Parabricks si un GPU dispose d'au moins `PARABRICKS_MIN_VRAM_GB` ; sinon GATK4 CPU, avec un avertissement dans les logs et `/health` ;
- `parabricks` : force le GPU ;
- `cpu` : force GATK4.

Le `GPUManager` lit la VRAM réelle via `nvidia-smi`. Au-delà de `GPU_SHARED_VRAM_GB`,
Mistral et BioGPT restent chargés ensemble ; en dessous, ils sont chargés à tour de rôle.

## 5. Accès via AnyDesk et sécurité

Les données génomiques sont des **données de santé**. Par défaut :

- Les ports sont publiés **uniquement sur `127.0.0.1`**. Ouvrez le navigateur *du serveur* via AnyDesk sur
  **http://localhost:3000**.
- Ollama n'expose aucun port ; seul le backend lui parle, par le réseau Docker interne.
- Le navigateur n'appelle que le port 3000. Next.js relaie `/api/v1` vers le backend (même origine, donc pas de CORS).

**Ouvrir l'interface à d'autres postes du réseau local** (choix explicite) :
1. `Backend/.env` : `BIND_ADDRESS=<IP LAN du serveur>` (ex. `192.168.1.50`), et ajouter
   `http://192.168.1.50:3000` à `CORS_ORIGINS`.
2. Pare-feu : `sudo ufw allow from 192.168.1.0/24 to any port 3000 proto tcp`.
3. `bash scripts/stop.sh && bash scripts/start.sh`.

Aucun port ne doit jamais être ouvert sur Internet. Pas de redirection de port sur la box ou le routeur.

**Bonnes pratiques AnyDesk** :
- accès non surveillé protégé par un **mot de passe long et unique** ;
- **liste blanche** (Access Control List) limitée aux ID AnyDesk autorisés ;
- **authentification à deux facteurs** activée ;
- AnyDesk à jour, session verrouillée à la déconnexion, journal des connexions consulté régulièrement.

**Autres points** :
- `Backend/.env` en `chmod 600`, jamais commité (voir `.gitignore`).
- Le backend monte `/var/run/docker.sock`, ce qui équivaut à un accès root sur l'hôte. Ne l'exposez jamais hors du serveur ou du LAN.
- Chiffrez le disque `/data` (LUKS) et sauvegardez `patients/` selon votre politique RGPD/HDS.

## 6. Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| `/health` → `pipeline_backend: cpu` alors qu'il y a un GPU | VRAM < `PARABRICKS_MIN_VRAM_GB`, ou GPU non visible dans le conteneur | `nvidia-smi` sur l'hôte ; `docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi` ; installer le NVIDIA Container Toolkit puis `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| `could not select device driver "nvidia"` | Container Toolkit absent | Idem ci-dessus, ou `ZAYNB_GPU=0 bash scripts/start.sh` pour démarrer sans GPU |
| Parabricks `out of memory` (fq2bam) | GPU 16 Go à la limite | `PARABRICKS_LOW_MEMORY=true` dans `Backend/.env` |
| Conteneur Parabricks tué (OOM hôte) | RAM insuffisante | Réduire `PARABRICKS_MEMORY_GB` (≈ RAM − 8 Go) |
| BioGPT / Mistral `CUDA out of memory` | VRAM partagée insuffisante | Mettre `GPU_SHARED_VRAM_GB` au-dessus de la VRAM réelle pour forcer l'alternance, garder `OLLAMA_KEEP_ALIVE=0` |
| Interface : « Network Error » / erreur CORS | Accès depuis un autre poste sans configuration LAN | Voir section 5 : `BIND_ADDRESS`, `CORS_ORIGINS`, puis redémarrer |
| Frontend OK mais `/api/v1` en 502 | Backend pas prêt ou en échec | `bash scripts/logs.sh backend` (le 1er démarrage peut prendre plusieurs minutes) |
| « Ollama injoignable » / assistant muet | Conteneur ollama arrêté ou modèle absent | `bash scripts/logs.sh ollama` ; `bash scripts/pull_models.sh` ; `docker compose -p zaynb ps` |
| `Fichier introuvable sur le serveur` / `Chemin hors de LOCAL_DATA_ROOT` | Mauvais chemin saisi | Déposer le fichier sous `/data/zaynb/patients/<ID>/input/` et saisir le chemin absolu |
| Job FASTQ : référence manquante | hg38 non téléchargée | `bash scripts/download_reference.sh` puis `bash scripts/check_prereqs.sh` |
| Job en échec « VCF non annoté … base ClinVar absente » | ClinVar non téléchargé | `bash scripts/download_reference.sh --clinvar-only` puis relancer |
| Risque INDÉTERMINÉ | Variant pathogène sous les seuils QC (QUAL, DP, VAF) ou perte de fonction non classée | Voir « Variants à confirmer » dans le rapport ; confirmer par une seconde technique (Sanger) |
| `Impossible de créer /data/zaynb/...` | Droits | `sudo chown -R $USER: /data/zaynb` |
| `permission denied ... docker.sock` | Utilisateur hors du groupe docker | `sudo usermod -aG docker $USER`, puis se reconnecter |

## 7. Limites connues

- **Performances sans GPU ≥ 16 Go** : l'alignement BWA-MEM sur CPU prend plusieurs heures pour un génome
  (≈ 45 min mesurées pour ≈ 6 Gb sur 8 cœurs) ; l'appel de variants, restreint au panel, reste court.
  Un GPU grand public de moins de 16 Go (ex. RTX 2050 de 4 Go) ne peut pas faire tourner Parabricks ; il sert seulement à BioGPT.
- **Périmètre clinique** : variants ponctuels et petits indels classés dans ClinVar ; pas de CNV ni de grands
  réarrangements ; couverture du panel non mesurée (voir les limites dans chaque rapport).
- **Parabricks** : les commandes suivent la documentation Parabricks 4.6 mais n'ont pas pu être exécutées
  sans GPU compatible ; valider sur le serveur avec un petit jeu FASTQ (`smoke_test.sh --fastq`).
- **WSL2** : utilisable pour tester, déconseillé en production (performances disque, accès GPU dans Docker).
- **Redémarrage** : l'état des jobs est conservé (`tmp/jobs`) ; un job en cours au moment d'un redémarrage est marqué « interrompu » et peut être relancé (les étapes terminées sont reprises).
- **Un job à la fois** : les jobs sont exécutés en série (un seul worker de pipeline), ce qui est voulu pour protéger la VRAM.
