# ── Stage 1: deps ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app

# System deps needed by parsers (pdfplumber, lxml) and asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency declaration (cache layer — only invalidated on pyproject change)
COPY pyproject.toml LICENSE ./
# Stub src so hatchling can build the wheel metadata
RUN mkdir -p src

# Install directly from pyproject.toml — bypasses uv.lock so no transient
# sentence-transformers / torch / CUDA packages sneak in
RUN pip install --no-cache-dir .

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

