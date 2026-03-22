<div align="center">

# Synatyx

**A production-ready Context Engine for LLMs.**
Persistent, structured, relevance-scored memory — across every conversation.

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-11%20tools-purple)](docs/local-setup.md)
[![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker&logoColor=white)](docker-compose.yml)

</div>

---

## The Problem

LLMs forget everything between conversations. Every new session starts from zero — no memory of past decisions, preferences, or context. Synatyx solves this.

## How It Works

Synatyx sits between your IDE and any LLM. It intercepts conversations, stores knowledge in a 4-layer memory architecture, scores everything by relevance, and feeds the right context back automatically — within your token budget.

```
Your IDE  ──►  Synatyx (MCP)  ──►  LLM
                   │
           ┌───────┴────────┐
           │  4-Layer Memory │
           │  L1 Redis       │  working memory  (~4k tokens)
           │  L2 Qdrant      │  session summaries (~1k tokens)
           │  L3 Qdrant      │  semantic knowledge (~2k tokens)
           │  L4 Qdrant      │  permanent rules  (~500 tokens)
           └────────────────┘
```

---

## Features

- **11 MCP Tools** — store, retrieve, summarize, score, checkpoint, deprecate, list, ingest, task management
- **4-Layer Memory** — Redis (L1 working) + Qdrant (L2–L4 vector) + Postgres (sessions, tasks, audit)
- **Parser System** — ingest `.docx`, `.pdf`, `.md`, source code (`.py`, `.ts`, `.go`, `.rs`, …), and any URL
- **Checkpoint System** — named, pinned decision snapshots with soft deprecation (never deleted)
- **Task Mechanism** — persistent cross-session task tracking with priority and status
- **Hybrid Retrieval** — dense vectors + BM25 sparse + MMR diversity, fused into a single ranked list
- **Token Budget Manager** — auto-allocates context per layer, respects model limits
- **GraphQL API** — queries, mutations, real-time subscriptions for external services
- **Production Ready** — Docker Compose, Dockerfile, Alembic migrations, health checks, swap config

---

## MCP Tools

| Category | Tool | What it does |
|---|---|---|
| **Memory** | `context_store` | Save a fact, decision, or note |
| | `context_retrieve` | Hybrid semantic search across all layers |
| | `context_summarize` | Compress L1 → L2 episodic vector via LLM |
| | `context_score` | Re-rank a list of items against a query |
| **Knowledge** | `context_checkpoint` | Named pinned snapshot — importance = 1.0 |
| | `context_deprecate` | Mark superseded items — never deleted |
| | `context_list` | Browse stored items without vector search |
| | `context_ingest` | Parse any file or URL → auto-chunk → store |
| **Tasks** | `context_task_add` | Add a task to remember for later |
| | `context_task_list` | List tasks by status, priority, project |
| | `context_task_update` | Update status, priority, or description |

---

## Tech Stack

| Component | Technology |
|---|---|
| Core | Python 3.12 + asyncio |
| MCP Transport | Anthropic MCP SDK — JSON-RPC 2.0 / stdio |
| GraphQL | Strawberry — async-first, type-safe |
| Vector DB | Qdrant |
| Working Memory | Redis |
| Metadata + Tasks | PostgreSQL + Alembic |
| Embeddings + LLM | OpenAI `text-embedding-3-small` + `gpt-4o-mini` |

---

## Quick Start

→ **[Full Local Setup Guide](docs/local-setup.md)**

```bash
git clone https://github.com/tanerincode/synatyx.git && cd synatyx
cp .env.example .env          # set EMBEDDING_OPENAI_API_KEY
docker compose up -d qdrant redis postgres
uv sync && alembic upgrade head
python main.py                # starts MCP stdio server
```

Connect to Augment, Cursor, or Claude Code — see [docs/local-setup.md](docs/local-setup.md) for IDE config.

---

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
│   └── models/        # context, session, task, memory layer
├── docs/
│   └── local-setup.md
├── alembic/           # database migrations
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

---

## License

MIT © [Taner Bastaner](https://github.com/tanerincode)

