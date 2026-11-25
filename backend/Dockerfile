# Backend image: the API (default) and the scheduler (same image, different command).
# Podman or Docker; --format docker keeps the HEALTHCHECK (Podman's OCI default drops it).
#   podman build --format docker -t rap-backend backend
#   podman run -p 8000:8000 --env-file .env rap-backend
#   podman run --env-file .env rap-backend python -m recruiting.scheduler
# Settings come from RAP_* environment variables (see .env.example); no secrets in the image.

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM docker.io/library/python:3.12.16-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first (cached until uv.lock changes), then the project itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra embeddings --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra embeddings --no-editable
# The semantic-search model (RAP_EMBEDDER=local), downloaded once here so the running
# container never fetches anything. About 70 MB (quantised ONNX, MIT licence).
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN /app/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('${EMBEDDING_MODEL}', cache_dir='/opt/models')"

FROM docker.io/library/python:3.12.16-slim-bookworm
RUN useradd --system --uid 10001 --home-dir /app rap
WORKDIR /app
COPY --from=build --chown=rap:rap /app/.venv /app/.venv
COPY --from=build --chown=rap:rap /opt/models /opt/models
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1     RAP_EMBEDDING_CACHE_DIR=/opt/models HF_HUB_OFFLINE=1
USER rap
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
# Behind a load balancer, set FORWARDED_ALLOW_IPS to its address range so client IPs in the
# access log (S26) are the real ones; by default only 127.0.0.1 is trusted.
CMD ["uvicorn", "recruiting.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
