# Running Synatyx Locally

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Docker | 24+ | With Docker Compose v2 |
| Python | 3.12+ | |
| uv | latest | `pip install uv` |
| OpenAI API Key | — | For embeddings + LLM summarization |

---

## 1. Clone & configure

```bash
git clone https://github.com/tanerincode/synatyx.git
cd synatyx
cp .env.example .env
```

Open `.env` and set at minimum:

```env
EMBEDDING_OPENAI_API_KEY=sk-...
RUN_MODE=mcp
```

> **Embedding model** is configured via `EMBEDDING_MODEL` (provider-agnostic).
> Default: `text-embedding-3-small` (OpenAI). For local/free: set `EMBEDDING_PROVIDER=sentence-transformers` and `EMBEDDING_MODEL=all-MiniLM-L6-v2`.

---

## 2. Start everything (recommended)

```bash
make
```

This starts all services (Qdrant, Redis, Postgres, runs migrations, starts Synatyx) and tails the logs. That's it.

---

## 2a. Manual / local dev

Start infrastructure only:

```bash
make up
```

---

## 5. Connect your IDE

### Augment Code

Add to your Augment MCP settings:

```json
{
  "mcpServers": {
    "Synatyx": {
      "url": "http://localhost:9000/mcp/sse",
      "type": "sse"
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "synatyx": {
      "url": "http://localhost:9000/mcp/sse"
    }
  }
}
```

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "synatyx": {
      "url": "http://localhost:9000/mc/sse"
    }
  }
}
```

---

## 6. Available MCP tools

→ **[Full MCP Tools Reference](mcp-tools.md)**

| Category | Tool | What it does |
|---|---|---|
| **Project** | `context_set_project` | Activate a project — all ops scoped to `ctx_<slug>` |
| | `context_get_project` | Get active project or detect from workspace folder |
| **Memory** | `context_store` | Save a fact, decision, or note to memory |
| | `context_retrieve` | Hybrid semantic search across all memory layers |
| | `context_summarize` | Compress L1 working memory → L2 episodic vector |
| | `context_score` | Re-rank a list of items by relevance to a query |
| **Knowledge** | `context_checkpoint` | Save a named, pinned snapshot (importance = 1.0) |
| | `context_deprecate` | Mark an item as superseded (never deleted) |
| | `context_list` | Browse stored items without vector search |
| | `context_ingest` | Parse any file or URL and store as chunks |
| **Tasks** | `context_task_add` | Add a task to remember for later |
| | `context_task_list` | List tasks by status, priority, project |
| | `context_task_update` | Update status, priority, or description |
| **Skills** | `context_skill_store` | Save an agent skill definition (PG + Qdrant L3) |
| | `context_skill_find` | RAG search for the best matching skill |
| | `context_skill_get` | Fetch a skill by name or slug |
| | `context_skill_list` | List all stored skills |
| | `context_skill_delete` | Remove a skill + deprecate its embedding |

---

## 7. Verify everything works

```bash
python - <<'EOF'
import asyncio
from src.config import settings
from src.storage.qdrant import QdrantStorage
from src.storage.redis import RedisStorage

async def check():
    r = RedisStorage(url=settings.redis.url)
    print("Redis:", await r.ping())
    q = QdrantStorage(host=settings.qdrant.host, port=settings.qdrant.port, collection_name=settings.qdrant.collection_name)
    print("Qdrant:", await q.ping())

asyncio.run(check())
EOF
```

Expected output:
```
Redis: True
Qdrant: True
```

---

## 8. Makefile reference

| Command | What it does |
|---|---|
| `make` / `make run` | Start all services + tail synatyx logs |
| `make up` | Start all services detached |
| `make down` | Stop and remove containers |
| `make restart` | Down then up |
| `make logs` | Tail synatyx logs |
| `make migrate` | Run Alembic migrations inside Docker |
| `make build` | Rebuild Docker images |
| `make install` | Create venv + install deps |
| `make mcp` | Run MCP stdio server locally |
| `make dev` | Run GraphQL server locally |
| `make lint` | Run ruff linter |
| `make format` | Run ruff formatter |
| `make typecheck` | Run mypy |
| `make test` | Run pytest |
| `make check` | lint + typecheck + test |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Redis ping failed` | `docker compose up -d redis` |
| `Qdrant collection not found` | It auto-creates on first store — check `QDRANT_HOST` in `.env` |
| `alembic.ini not found` | Run from the project root directory |
| `EMBEDDING_OPENAI_API_KEY not set` | Add it to `.env` — required for OpenAI embeddings and summarization |
| `alembic can't connect to postgres` | Run `make migrate` — migrations run inside Docker where postgres is reachable |

