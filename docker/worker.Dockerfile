# Inference worker (CPU). Runs RQ jobs: prostate_infer + backend task code.
# GPU variant: docker/worker-cuda.Dockerfile
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cpu
COPY inference/pyproject.toml /src/inference/pyproject.toml
COPY backend/requirements.txt /src/backend/requirements.txt
# install only the dependencies first (cached layer), the package itself comes later
RUN python -c "import tomllib;print('\n'.join(tomllib.load(open('/src/inference/pyproject.toml','rb'))['project']['dependencies']))" \
      | grep -v '^torch' > /tmp/infer-reqs.txt \
 && echo 'torch==2.14.0' > /tmp/constraints.txt && echo 'torchvision==0.29.0' >> /tmp/constraints.txt \
 && pip install -c /tmp/constraints.txt -r /tmp/infer-reqs.txt -r /src/backend/requirements.txt

FROM deps AS test
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements-dev.txt /src/backend/requirements-dev.txt
RUN pip install -r /src/backend/requirements-dev.txt -c /tmp/constraints.txt
COPY inference /src/inference
COPY backend /src/backend
RUN pip install --no-deps -e /src/inference
WORKDIR /src
ENV PYTHONPATH=/src

FROM python:${PYTHON_VERSION}-slim AS runtime
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends libglib2.0-0 libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /models /data && chown app:app /models /data
COPY --from=deps /opt/venv /opt/venv
COPY --chown=app:app inference /app/inference
COPY --chown=app:app backend /app/backend
RUN /opt/venv/bin/pip install --no-deps --no-cache-dir /app/inference \
 && /opt/venv/bin/pip uninstall -y -q setuptools || true
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app PYTHONUNBUFFERED=1 \
    MODEL_DIR=/models/bundle MODEL_CACHE_DIR=/models/cache HF_HOME=/models/cache/hf
USER app
WORKDIR /app
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD python -m backend.worker.healthcheck || exit 1
CMD ["python", "-m", "backend.worker.run"]
