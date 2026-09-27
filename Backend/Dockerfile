# GermlineIQ backend — FastAPI + LangGraph orchestrator + BioGPT (CUDA or CPU)
# GATK / Parabricks containers are started on the host Docker daemon
# (socket mounted by docker-compose.local.yml); bwa/samtools run here (CPU pipeline).
# PyTorch built for CUDA 12.8: required by RTX 50xx (Blackwell, sm_120) GPUs, also runs on older GPUs and CPU.
FROM docker:27-cli AS dockercli

FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

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
