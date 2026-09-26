# Inference worker for NVIDIA GPUs (CUDA 12.8 runtime). fp16 autocast is used automatically (as in NB01).
FROM pytorch/pytorch:2.9.0-cuda12.8-cudnn9-runtime AS runtime
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends libglib2.0-0 libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /models /data && chown app:app /models /data
COPY inference/pyproject.toml /tmp/pyproject.toml
COPY backend/requirements.txt /tmp/backend-requirements.txt
RUN python -c "import tomllib;print('\n'.join(tomllib.load(open('/tmp/pyproject.toml','rb'))['project']['dependencies']))" \
      | grep -v '^torch' > /tmp/infer-reqs.txt \
 && pip install -r /tmp/infer-reqs.txt -r /tmp/backend-requirements.txt
COPY --chown=app:app inference /app/inference
COPY --chown=app:app backend /app/backend
RUN pip install --no-deps /app/inference
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 MODEL_DIR=/models/bundle MODEL_CACHE_DIR=/models/cache HF_HOME=/models/cache/hf \
    INFER_NUM_WORKERS=4
USER app
WORKDIR /app
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
  CMD python -m backend.worker.healthcheck || exit 1
CMD ["python", "-m", "backend.worker.run"]
