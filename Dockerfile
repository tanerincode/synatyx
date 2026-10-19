# ── Stage 0: web dashboard (Vite build) ───────────────────────────────────────
# Node lives only in this stage; the runtime image stays python:3.12-slim and
# receives the static files in web/dist.
FROM node:22-slim AS web

WORKDIR /web

# Cache layer: only re-install when the lockfile changes
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
RUN npm run build

# ── Stage 1: deps ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app

# Build-time libs (gcc for asyncpg, libpq-dev for psycopg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Create an isolated venv so we can copy it cleanly to the runtime stage
RUN python3 -m venv /app/.venv

# Cache layer: only re-install when pyproject.toml changes
COPY pyproject.toml install_deps.py ./

# Read deps from pyproject.toml and install into the venv.
# Bypasses uv.lock entirely — sentence-transformers/torch/CUDA never pulled in.
RUN /app/.venv/bin/python3 install_deps.py

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime-only system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --gid 1001 synatyx \
    && useradd --uid 1001 --gid synatyx --shell /bin/bash --create-home synatyx

# Copy venv from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy application source
COPY --chown=synatyx:synatyx . .

# Built dashboard (static files, served at /dashboard)
COPY --from=web --chown=synatyx:synatyx /web/dist /app/web/dist

# Build identity — surfaced on /health and the dashboard header
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER synatyx

# Migrations are handled by the dedicated `migrate` service in docker-compose.yml
ENTRYPOINT ["python", "main.py"]

