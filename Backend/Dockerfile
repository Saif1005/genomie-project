# Backend Zaynb — FastAPI + BioGPT (CUDA ou CPU) + orchestrateur LangGraph
# Client Docker : lance les conteneurs GATK / Parabricks sur le démon de l'hôte
# (socket /var/run/docker.sock monté par docker-compose.local.yml).
FROM docker:27-cli AS dockercli

FROM pytorch/pytorch:2.2.2-cuda12.1-cudnn8-runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl ca-certificates \
    zlib1g-dev libbz2-dev liblzma-dev libcurl4-openssl-dev \
    bwa samtools tabix \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

COPY requirements.txt requirements-api.txt requirements-llm.txt requirements-langchain.txt ./

# torch déjà fourni par l'image de base — installer le reste
RUN pip install --upgrade pip && \
    grep -v '^torch' requirements-llm.txt | grep -v '^torchvision' | grep -v '^torchaudio' | \
    grep -v '^--extra-index-url' > /tmp/req-llm-notorch.txt && \
    pip install -r requirements.txt && \
    pip install -r requirements-api.txt && \
    pip install -r /tmp/req-llm-notorch.txt && \
    pip install -r requirements-langchain.txt

COPY app/ ./app/
COPY src/ ./src/
COPY config/ ./config/
COPY data/cancer_genes/ ./data/cancer_genes/

RUN mkdir -p /mnt/data/work /mnt/data/scratch /mnt/data/references /app/models \
    /data/zaynb/reference/hg38 /data/zaynb/patients /data/zaynb/models /data/zaynb/tmp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
