# ZAYNB backend — FastAPI + orchestrateur LangGraph + BioGPT (CUDA ou CPU)
# Les conteneurs GATK / Parabricks sont lancés sur le démon Docker de l'hôte
# (socket monté par docker-compose.local.yml) ; bwa/samtools tournent ici (pipeline CPU).
FROM docker:27-cli AS dockercli

FROM pytorch/pytorch:2.2.2-cuda12.1-cudnn8-runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates bwa samtools tabix \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

COPY requirements.txt requirements-llm.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt -r requirements-llm.txt

COPY app/ ./app/
COPY src/ ./src/
COPY config/ ./config/
COPY data/cancer_genes/ ./data/cancer_genes/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
