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

- **4 MCP Tools** — `context_retrieve`, `context_store`, `context_summarize`, `context_score`
- **GraphQL API** — Queries, Mutations, and real-time Subscriptions for external services
- **Model Agnostic** — Anthropic and OpenAI adapter support out of the box
- **Relevance Scoring** — Recency, semantic similarity, importance, and user signal combined
- **Token Budget Manager** — Automatically allocates context space per memory layer
- **Async First** — Built on Python asyncio + FastAPI
- **Self-hosted** — No vendor lock-in, runs fully on your infrastructure

## Tech Stack

| Layer | Technology |
|---|---|
| Core | Python 3.12 + FastAPI + asyncio |
| MCP Transport | Anthropic MCP SDK (JSON-RPC 2.0) |
| GraphQL | Strawberry (async-first, type-safe) |
| Vector DB | Qdrant |
| Cache | Redis |
| Metadata DB | PostgreSQL |
| Message Queue | Kafka |
| Observability | OpenTelemetry + Grafana |

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.12+

### Run Infrastructure

```bash
docker compose up -d
```

This starts Qdrant, Redis, PostgreSQL, and Kafka.

### MCP Config (OpenClaw / Claude Desktop)

```json
{
  "mcpServers": {
    "synatyx": {
      "url": "http://localhost:8000/mcp",
      "tools": [
        "context_retrieve",
        "context_store",
        "context_summarize",
        "context_score"
      ]
    }
  }
}
```

## Project Structure

```
synatyx/
├── src/
│   ├── core/          # Business logic (retrieve, store, summarize, score, budget)
│   ├── transports/
│   │   ├── mcp/       # MCP server + Anthropic & OpenAI adapters
│   │   └── graphql/   # Strawberry schema, resolvers, subscriptions
│   ├── storage/       # Qdrant, Redis, PostgreSQL clients
│   └── models/        # Pydantic models
├── tests/
├── docker-compose.yml
└── pyproject.toml
```

## License

MIT

