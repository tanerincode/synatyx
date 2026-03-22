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
git clone https://github.com/tombastaner/taty-v2.git synatyx
cd synatyx
cp .env.example .env
```

Open `.env` and set at minimum:

```env
EMBEDDING_OPENAI_API_KEY=sk-...
RUN_MODE=mcp
```

---

## 2. Start infrastructure

```bash
docker compose up -d qdrant redis postgres
```

Wait until all three are healthy:

```bash
docker compose ps
```

You should see `healthy` next to qdrant, redis, and postgres.

---

## 3. Install Python deps & run migrations

```bash
uv sync
alembic upgrade head
```

---

## 4. Start the server

**MCP mode** (stdio — for IDE integrations):

```bash
RUN_MODE=mcp python main.py
```

**GraphQL mode** (HTTP — for external services):

```bash
RUN_MODE=graphql python main.py
# → http://localhost:8000/graphql
```

---

## 5. Connect your IDE

### Augment Code

Add to your Augment MCP settings:

```json
{
  "mcpServers": {
    "synatyx": {
      "command": "python",
      "args": ["/absolute/path/to/synatyx/main.py"],
      "env": { "RUN_MODE": "mcp" }
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
      "command": "python",
      "args": ["/absolute/path/to/synatyx/main.py"],
      "env": { "RUN_MODE": "mcp" }
    }
  }
}
```

### Claude Code

Add to your Claude Code MCP config (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "synatyx": {
      "command": "python",
      "args": ["/absolute/path/to/synatyx/main.py"],
      "env": { "RUN_MODE": "mcp" }
    }
  }
}
```

---

## 6. Available MCP tools

| Tool | What it does |
|---|---|
| `context_store` | Save a fact, decision, or note to memory |
| `context_retrieve` | Semantic search across all memory layers |
| `context_summarize` | Compress L1 working memory → L2 episodic vector |
| `context_score` | Re-rank a list of items by relevance to a query |
| `context_checkpoint` | Save a named, pinned snapshot (importance = 1.0) |
| `context_deprecate` | Mark an item as superseded (never deleted) |
| `context_list` | Browse stored items without vector search |
| `context_ingest` | Parse any file or URL and store as chunks |

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
    q = QdrantStorage(host=settings.qdrant.host, port=settings.qdrant.port)
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

## 8. Full Docker stack (optional)

To run everything including the Synatyx server in Docker:

```bash
docker compose up -d
```

The `synatyx` service waits for all dependencies to be healthy before starting.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Redis ping failed` | `docker compose up -d redis` |
| `Qdrant collection not found` | It auto-creates on first store — check `QDRANT_HOST` in `.env` |
| `alembic.ini not found` | Run from the project root directory |
| `EMBEDDING_OPENAI_API_KEY not set` | Add it to `.env` — required for embeddings and summarization |

