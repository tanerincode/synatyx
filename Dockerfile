# ── Stage 1: deps ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app

# System deps needed by parsers (pdfplumber, lxml)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dep resolution
RUN pip install --no-cache-dir uv

# Copy dependency files only (cache layer)
COPY pyproject.toml uv.lock* LICENSE ./

# Install production deps into /app/.venv
RUN uv sync --no-dev --no-install-project

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --gid 1001 synatyx \
    && useradd --uid 1001 --gid synatyx --shell /bin/bash --create-home synatyx

# Copy installed packages from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy application source
COPY --chown=synatyx:synatyx . .

# Make venv the active Python
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER synatyx

# Entrypoint: run migrations then start the server
ENTRYPOINT ["sh", "-c", "alembic upgrade head && python main.py"]

