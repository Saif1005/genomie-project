# GermlineIQ — Deployment on a local server (on-premise, remote access via AnyDesk)

This guide installs GermlineIQ (FastAPI backend, Next.js frontend, Ollama/Mistral, BioGPT,
GATK/Parabricks pipeline) on **a physical server**, without any cloud service.
All reference data are public: hg38 and known sites (GATK Resource Bundle, Broad Institute),
ClinVar (NCBI, public domain).

Two installation modes are supported:

- **Docker Compose** (sections 1–7) — recommended for production;
- **Native (conda)** (section 8) — used on development/demo machines, including WSL2.

---

## 1. Requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Linux x86_64 (Ubuntu 22.04 / 24.04 tested) | Native Ubuntu 22.04 LTS |
| CPU | 8 cores | ≥ 16 cores |
| RAM | 16 GB (VCF mode) / 32 GB (FASTQ) | ≥ 64 GB |
| Disk | 200 GB free | ≥ 1 TB SSD |
| GPU (optional) | — | NVIDIA ≥ 16 GB VRAM, driver ≥ 525 (Parabricks 4.6) |
| Software | Docker Engine + Docker Compose v2, curl, python3 | + NVIDIA Container Toolkit if GPU |
| Network | Outbound Internet for the installation (images, models, reference) | — |

**Without a GPU, or with a GPU below 16 GB of VRAM**, the FASTQ pipeline automatically falls back to
GATK4 on CPU. BWA-MEM alignment then takes several hours for a whole genome, but variant calling
is restricted to the panel. Direct VCF mode stays fast (seconds).

## 2. Data layout

All data live under `LOCAL_DATA_ROOT` (default `/data/germlineiq`):

```
/data/germlineiq/
├── reference/hg38/        # FASTA, .fai, .dict, BWA index, BQSR known sites
├── reference/clinvar/     # ClinVar GRCh38 + panel index (cache)
├── reference/panels/      # panel BED (generated)
├── reference/1000g/       # demonstration cohort manifest and carrier scan (optional)
├── reference/giab/        # GIAB HG001 truth set for the analytical benchmark (optional)
├── patients/<ID>/input/   # FASTQ R1/R2, VCF files provided by the user
├── patients/<ID>/output/  # BAM, VCF, JSON artifacts, reports
├── models/
│   ├── ollama/            # Mistral models (Ollama volume)
│   ├── huggingface/       # BioGPT cache (HF_HOME)
│   ├── biogpt_corpus/     # frozen PubMed corpus for fine-tuning
│   └── biogpt-germlineiq-lora/  # LoRA adapters, training logs, evaluations
├── benchmarks/            # latest benchmark results (served by the API)
└── tmp/                   # work files, result cache, job state (tmp/jobs)
```

This directory is mounted **at the same path** inside the backend container. This is required,
because the GATK/Parabricks containers are started by the host Docker daemon.

## 3. Step-by-step installation (Docker Compose)

```bash
# 0. Get the code
git clone <repository-url> germlineiq && cd germlineiq

# 1. Diagnostics (read-only): OK / WARN / KO + suggested fix
bash scripts/check_prereqs.sh

# 2. Data directory
sudo mkdir -p /data/germlineiq && sudo chown -R $USER: /data/germlineiq

# 3. Configuration (created automatically by start.sh if missing)
cp Backend/.env.local.example Backend/.env && chmod 600 Backend/.env
#    → review: LOCAL_DATA_ROOT, PARABRICKS_MEMORY_GB (≈ RAM − 8 GB), BIND_ADDRESS

# 4. Build and start (adds docker-compose.gpu.yml if a GPU is usable by Docker)
bash scripts/start.sh

# 5. Models: Mistral (~4 GB), BioGPT (~1.6 GB), GATK image (~2 GB), Parabricks if GPU ≥ 16 GB
bash scripts/pull_models.sh

# 6. Public reference data
bash scripts/download_reference.sh --clinvar-only   # ClinVar (~0.2 GB): required, enough for VCFs
bash scripts/download_reference.sh                  # + hg38 (≈ 9.7 GB; ≈ 21 GB with --with-dbsnp) for FASTQ

# 7. End-to-end verification
bash scripts/check_prereqs.sh
bash scripts/smoke_test.sh
```

Steps 5 and 6 ask for confirmation before downloading (`--yes` to automate).
`download_reference.sh` resumes where it stopped when rerun, and checks sizes and MD5 sums.
ClinVar is released weekly: `bash scripts/download_reference.sh --clinvar-only --refresh-clinvar`
updates it (the release used is written into every report).

## 4. Operation

| Command | Purpose |
|---|---|
| `bash scripts/start.sh [--no-build]` | Starts ollama + backend + frontend, waits for `/health` |
| `bash scripts/stop.sh` | Stops everything (data and models are kept) |
| `bash scripts/logs.sh [backend\|frontend\|ollama]` | Follows the logs |
| `bash scripts/smoke_test.sh [--fastq R1 R2]` | Health + synthetic BRCA1/BRCA2 VCF analysis → HIGH risk expected (+ optional FASTQ) |
| `python scripts/expert_check.py <patients/ID/output>` | Independent re-computation of every report metric with bcftools/samtools |
| `cd Backend && python -m benchmarks.multiagent --suites all` | Full multi-agent benchmark (see README) |
| `curl http://127.0.0.1:8000/health` | Selected engine (gpu/cpu) and reason, detected GPUs, panel, ClinVar availability |
| `curl http://127.0.0.1:8000/api/v1/tools` | Orchestrator agents with their inputs/outputs |

**Running an analysis**: place the files in `/data/germlineiq/patients/<ID>/input/`, then enter the
**server path** in the interface (e.g. `/data/germlineiq/patients/P001/input/sample_R1.fastq.gz`).
Any path outside `LOCAL_DATA_ROOT` is refused. Each agent writes its result to
`patients/<ID>/output/` (`annotated_variants.json`, `panel_analysis.json`, `vcf_statistics.json`,
`alignment_qc.json`, report `REP-*.json`). Relaunching an interrupted job resumes variant calling
at the last completed step.

**Pipeline selection** (`PIPELINE_BACKEND` in `Backend/.env`):
- `auto` (default): Parabricks if a GPU has at least `PARABRICKS_MIN_VRAM_GB`; otherwise GATK4 on CPU,
  with a warning in the logs and in `/health`. If Parabricks fails during a run (typically host RAM
  exhausted on an exome or genome), the analysis is rerun automatically with GATK4 on CPU and the
  reason is written into the report (`pipeline_backend_reason`);
- `parabricks`: force the GPU (no fallback: a Parabricks failure fails the analysis);
- `cpu`: force GATK4.

The `GPUManager` reads the real VRAM through `nvidia-smi`. Above `GPU_SHARED_VRAM_GB`, Mistral and
BioGPT stay loaded together; below it, they are loaded in turn.

## 5. AnyDesk access and security

Genomic data are **health data**. By default:

- Ports are published **only on `127.0.0.1`**. Open the *server's* browser through AnyDesk at
  **http://localhost:3000**.
- Ollama exposes no port; only the backend talks to it, through the internal Docker network.
- The browser only calls port 3000. Next.js proxies `/api/v1` to the backend (same origin, no CORS).

**Opening the interface to other machines on the local network** (explicit choice):
1. `Backend/.env`: `BIND_ADDRESS=<server LAN IP>` (e.g. `192.168.1.50`), and add
   `http://192.168.1.50:3000` to `CORS_ORIGINS`.
2. Firewall: `sudo ufw allow from 192.168.1.0/24 to any port 3000 proto tcp`.
3. `bash scripts/stop.sh && bash scripts/start.sh`.

No port must ever be opened to the Internet. No port forwarding on the router.

**AnyDesk good practice**:
- unattended access protected by a **long, unique password**;
- **allow-list** (Access Control List) restricted to authorised AnyDesk IDs;
- **two-factor authentication** enabled;
- AnyDesk kept up to date, session locked on disconnect, connection log reviewed regularly.

**Other points**:
- `Backend/.env` in `chmod 600`, never committed (see `.gitignore`).
- The backend mounts `/var/run/docker.sock`, which is equivalent to root access on the host. Never
  expose it outside the server or the LAN.
- Encrypt the `/data` disk (LUKS) and back up `patients/` according to your GDPR/health-data policy.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health` → `pipeline_backend: cpu` despite a GPU | VRAM < `PARABRICKS_MIN_VRAM_GB`, `PIPELINE_BACKEND=cpu`, or GPU not visible in the container | `nvidia-smi` on the host; `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`; install the NVIDIA Container Toolkit then `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| `could not select device driver "nvidia"` | Container Toolkit missing | Same as above, or `GERMLINEIQ_GPU=0 bash scripts/start.sh` to start without GPU |
| Parabricks `out of memory` (fq2bam) | 16 GB GPU at its limit | `PARABRICKS_LOW_MEMORY=true` in `Backend/.env` |
| Parabricks container killed (host OOM, `fq2bam failed (code 255)`) | Not enough host RAM for the input size: on a 16 GB GPU, fq2bam needed more than 13 GB of RAM for an 8 GB exome FASTQ pair | Give the server more RAM (WSL2: raise `memory=` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`) and `PARABRICKS_MEMORY_GB` accordingly; in `auto` mode the job falls back to GATK4 on CPU |
| BioGPT / Mistral `CUDA out of memory` | Not enough shared VRAM | Set `GPU_SHARED_VRAM_GB` above the real VRAM to force alternation, keep `OLLAMA_KEEP_ALIVE=0` |
| BioGPT fails on RTX 50xx (Blackwell) in Docker | Image PyTorch too old (sm_120 needs CUDA ≥ 12.8) | Use a `pytorch` base image ≥ 2.7 with CUDA 12.8 in `Backend/Dockerfile` |
| Interface: "Network Error" / CORS error | Access from another machine without LAN configuration | See section 5: `BIND_ADDRESS`, `CORS_ORIGINS`, then restart |
| Frontend OK but `/api/v1` returns 502 | Backend not ready or failing | `bash scripts/logs.sh backend` (the first start can take several minutes) |
| "Ollama unreachable" / silent assistant | Ollama container stopped or model missing | `bash scripts/logs.sh ollama`; `bash scripts/pull_models.sh`; `docker compose -p germlineiq ps` |
| `File not found on the server` / `Path outside LOCAL_DATA_ROOT` | Wrong path entered | Place the file under `/data/germlineiq/patients/<ID>/input/` and enter the absolute path |
| FASTQ job: reference missing | hg38 not downloaded | `bash scripts/download_reference.sh` then `bash scripts/check_prereqs.sh` |
| Job failed "Unannotated VCF … ClinVar release missing" | ClinVar not downloaded | `bash scripts/download_reference.sh --clinvar-only` then relaunch |
| Risk INDETERMINATE | Pathogenic variant below QC thresholds (QUAL, DP, VAF), unclassified loss of function, or < 90 % of known pathogenic sites covered | See "Variants to confirm" and the quality warnings in the report; confirm with an orthogonal method (Sanger) or re-sequence |
| `Cannot create /data/germlineiq/...` | Permissions | `sudo chown -R $USER: /data/germlineiq` |
| `permission denied ... docker.sock` | User not in the docker group | `sudo usermod -aG docker $USER`, then log in again |

## 7. Known limitations

- **Performance without a GPU ≥ 16 GB**: CPU BWA-MEM alignment takes several hours for a genome;
  a whole exome (≈ 9 Gb) takes about 1 hour on 6 cores; variant calling restricted to the panel stays short.
- **Clinical scope**: SNVs and small indels classified in ClinVar; no CNVs or large rearrangements.
  Coverage is measured at every known pathogenic ClinVar site of the panel (FASTQ mode only).
- **Parabricks**: validated on the delivery server (RTX 5080 16 GB, Parabricks 4.6.0-1, WSL2 with
  18 GB RAM) on the 1000 Genomes panel samples: final VCF identical to GATK4 CPU (NA10842: 1,357/1,357
  calls, same genotypes and QUAL; HG00096: 1,704 shared raw calls, 0 GPU-only, 2 CPU-only),
  `expert_check.py` 20/20. The NA12878 exome (2 × 4 GB FASTQ) exceeds the RAM available to WSL2
  (OOM kill at 10 and 13 GB) and falls back to CPU.
- **WSL2**: usable for testing and demonstrations, not recommended for production (disk performance, GPU access in Docker).
- **Restart**: job state is kept (`tmp/jobs`); a job running at restart time is marked
  "interrupted" and can be relaunched (completed steps are resumed).
- **One job at a time**: jobs run sequentially (a single pipeline worker), on purpose, to protect VRAM.
- **Output ownership**: files written by the GATK container belong to `root`.

## 8. Native installation (conda, no Docker Compose)

Used when Docker Compose cannot be used for the application itself (e.g. WSL2 with Docker Desktop
only for the GATK containers). The backend then starts GATK containers through Docker Desktop.

```bash
# Miniconda + environment
conda create -n genomic python=3.11 -y && conda activate genomic
pip install -r Backend/requirements-dev.txt -r Backend/requirements-llm.txt
pip install torch --index-url https://download.pytorch.org/whl/cu128     # RTX 50xx needs CUDA ≥ 12.8
conda install -y -c conda-forge -c bioconda nodejs=20 samtools bcftools htslib bwa gatk4 bedtools
(cd Frontend && npm ci && npm run build)

# Environment variables of the conda env
conda env config vars set -n genomic LOCAL_DATA_ROOT=$HOME/germlineiq_data \
  HF_HOME=$HOME/germlineiq_data/models/huggingface OLLAMA_HOST=http://localhost:11434 \
  PYTHONPATH=$PWD/Backend

# Ollama in user space (no sudo): download the Linux archive into ~/.local/ollama, then
OLLAMA_MODELS=$HOME/germlineiq_data/models/ollama/models ~/.local/ollama/bin/ollama serve &
~/.local/ollama/bin/ollama pull mistral:v0.3
```

Start/stop in native mode:

```bash
bash scripts/start_native.sh                     # Ollama + backend + frontend
bash scripts/start_native.sh --restart-backend   # after editing Backend/.env
bash scripts/start_native.sh --restart-frontend  # after rebuilding the frontend
bash scripts/start_native.sh --stop              # stop everything (data kept)
```

### Automatic start (systemd user service)

```bash
bash scripts/install_autostart.sh        # installs and enables ~/.config/systemd/user/germlineiq.service
systemctl --user start germlineiq        # start now; also: stop | restart | status
sudo loginctl enable-linger $USER        # once: start at boot without an open terminal
```

On WSL2, WSL must itself be running: create the Windows logon task printed by the script
(`schtasks /create /tn GermlineIQ-WSL /sc onlogon … wsl.exe -d Ubuntu -u <user> -- sleep infinity`)
and enable "Start Docker Desktop when you sign in". The unit adds `/usr/lib/wsl/lib` to `PATH` so
that `nvidia-smi` (GPU detection → Parabricks) is found under systemd.
Service logs: `$LOCAL_DATA_ROOT/tmp/{backend,frontend,ollama}.log`; start/stop log: `journalctl --user -u germlineiq`.

## 9. Real demonstration data (1000 Genomes, open access)

```bash
python scripts/find_1000g_carriers.py --out $LOCAL_DATA_ROOT/reference/1000g/carriers.tsv
python scripts/prepare_1000g_cohort.py --carriers $LOCAL_DATA_ROOT/reference/1000g/carriers.tsv
```

The first script scans the 3,202 high-coverage 1000 Genomes genomes for ClinVar P/LP variants in
the 13 panel genes (remote tabix queries); the second streams the real reads of a selected cohort
over the panel genes from the public CRAM files and writes paired FASTQ files into
`patients/<SAMPLE>/input/`, with a manifest holding the ground truth used by the benchmark.

## 10. Delivery checklist

Run on the delivery server, in this order (every step prints PASS/FAIL or a health line):

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | Services up | `systemctl --user status germlineiq` | `active` |
| 2 | Health | `curl -s localhost:8000/health` | `status: ok`, `pipeline_backend: parabricks` (GPU ≥ 16 GB) or `cpu`, `clinvar.available: true` |
| 3 | End to end | `bash scripts/smoke_test.sh --fastq <R1> <R2>` | `Smoke test passed.` |
| 4 | Unit and integration tests | `cd Backend && python -m pytest -q` | all passed |
| 5 | Independent verification of a report | `python scripts/expert_check.py $LOCAL_DATA_ROOT/patients/<ID>/output` | `20/20 checks agree` |
| 6 | Benchmark | `cd Backend && python -m benchmarks.multiagent --suites all` | results in the **Benchmarks** page |
| 7 | Statistics interpreter | `curl -s localhost:8000/api/v1/models/biogpt` | `statistics_model.in_service: true`, promotion gate passed |
| 8 | File ownership | `find $LOCAL_DATA_ROOT -user 0 \| wc -l` | `0` (containers run as the service user) |

Docker Compose deployment: the backend image is built on PyTorch 2.8 / CUDA 12.8
(`pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime`), validated on an RTX 5080 (sm_120); set
`CONTAINER_USER=<uid>:<gid>` of the host user in `Backend/.env`.

