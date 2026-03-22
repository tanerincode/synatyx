# Synatyx

**Synatyx** is an open-source, production-ready **Context Engine** for LLMs — a smart memory layer that gives stateless language models persistent, structured, and relevance-scored memory across conversations.

## Why Synatyx?

LLMs forget everything between conversations. Synatyx solves this with a layered memory architecture that collects past knowledge, scores it by relevance, compresses it to fit the token budget, and feeds it back to the model — automatically.

## Memory Architecture

| Layer | Name | Description | Token Budget |
|---|---|---|---|
| L1 | Working Memory | Last 10–20 messages, always included | ~4k |
| L2 | Episodic Memory | Session summaries | ~1k |
| L3 | Semantic Memory | Vector similarity search from past conversations | ~2k |
| L4 | Procedural Memory | Permanent user preferences and rules | ~500 |

## Features

- **8 MCP Tools** — store, retrieve, summarize, score, checkpoint, deprecate, list, ingest
- **Parser System** — ingest `.docx`, `.pdf`, `.md`, code files (`.py`, `.ts`, `.go`, …), and any URL
- **4-Layer Memory** — L1 Redis working memory, L2–L4 Qdrant vector store
- **Checkpoint System** — named pinned snapshots with deprecation (never deleted)
- **Relevance Scoring** — recency, semantic similarity, importance, and user signal combined
- **Token Budget Manager** — automatically allocates context space per memory layer
- **GraphQL API** — queries, mutations, and real-time subscriptions for external services
- **Async First** — built on Python asyncio + FastAPI
- **Self-hosted** — no vendor lock-in, runs fully on your infrastructure

## Tech Stack

| Layer | Technology |
|---|---|
| Core | Python 3.12 + asyncio |
| MCP Transport | Anthropic MCP SDK (JSON-RPC 2.0 / stdio) |
| GraphQL | Strawberry (async-first, type-safe) |
| Vector DB | Qdrant |
| Working Memory | Redis |
| Metadata DB | PostgreSQL + Alembic |
| Observability | OpenTelemetry |

## MCP Tools

| Tool | Description |
|---|---|
| `context_store` | Save a fact or decision to memory |
| `context_retrieve` | Semantic search across all memory layers |
| `context_summarize` | Compress L1 working memory → L2 episodic vector |
| `context_score` | Re-rank a list of items by relevance |
| `context_checkpoint` | Save a named, pinned snapshot (importance = 1.0) |
| `context_deprecate` | Mark an item as superseded — never deleted |
| `context_list` | Browse items without vector search |
| `context_ingest` | Parse any file or URL and store as chunks |

## Getting Started

→ **[Local Setup Guide](docs/local-setup.md)**

Quick start:

```bash
cp .env.example .env        # add your EMBEDDING_OPENAI_API_KEY
docker compose up -d qdrant redis postgres
uv sync && alembic upgrade head
python main.py
```

## Project Structure

```
synatyx/
├── src/
│   ├── core/          # retrieve, store, summarize, score, ingest, budget
│   ├── parsers/       # docx, pdf, markdown, code, web + registry
│   ├── transports/
│   │   ├── mcp/       # MCP stdio server, tools.json, adapters
│   │   └── graphql/   # Strawberry schema, resolvers, subscriptions
│   ├── storage/       # Qdrant, Redis, PostgreSQL clients
│   └── models/        # Pydantic models
├── docs/
│   └── local-setup.md
├── tests/
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## License

MIT

