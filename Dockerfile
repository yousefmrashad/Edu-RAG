FROM python:3.13-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 10001 app

# Dependencies only, so this layer is cached until the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# --- api --------------------------------------------------------------- #
FROM base AS api
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

USER app
CMD ["uvicorn", "edu_rag.api:app", "--host", "0.0.0.0", "--port", "8000"]

# --- ingest ------------------------------------------------------------ #
FROM base AS ingest-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra ingest

FROM ingest-deps AS ingest
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra ingest

USER app
CMD ["celery", "-A", "edu_rag.worker:app", "worker", \
     "-Q", "ingest", "--concurrency=1", "--prefetch-multiplier=1", \
     "--time-limit=1800", "--soft-time-limit=1500", \
     "--max-tasks-per-child=20", "--loglevel=INFO"]
