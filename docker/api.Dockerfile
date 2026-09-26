# FastAPI backend (no PyTorch: the API only validates slides, serves tiles and enqueues jobs).
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY inference /src/inference
# prostate_infer provides slide validation/QC/schemas here; its torch parts are never imported by the API
RUN pip install --no-deps /src/inference

FROM python:${PYTHON_VERSION}-slim AS runtime
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends libglib2.0-0 libpq5 curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /data && chown app:app /data
COPY --from=deps /opt/venv /opt/venv
RUN /opt/venv/bin/pip uninstall -y -q setuptools || true
COPY --chown=app:app backend /app/backend
COPY --chown=app:app docker/api-entrypoint.sh /app/entrypoint.sh
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    STORAGE_DIR=/data/storage SLIDE_CACHE_DIR=/data/slide-cache
USER app
WORKDIR /app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
