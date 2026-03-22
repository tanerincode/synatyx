<div align="center">

# Synatyx

**The memory layer your AI agents have been missing.**

Give your LLM a persistent, structured, relevance-scored memory — that survives every conversation, every session, every project.

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-18%20tools-purple)](docs/mcp-tools.md)
[![Docker](https://img.shields.io/badge/Docker-ready-blue?logo=docker&logoColor=white)](docker-compose.yml)

</div>

---

## The Problem

LLMs are stateless. Every new conversation starts from zero — no memory of past decisions, preferences, or project context. You repeat yourself constantly. Your AI assistant forgets everything you taught it yesterday.

**Synatyx fixes that.**

---

## What It Does

Synatyx is a **Context Engine** that plugs into any MCP-compatible AI client (Augment Code, Cursor, Claude Desktop, Claude Code) and gives it persistent, structured memory across all your conversations.

```mermaid
flowchart LR
    IDE(["🖥️ Your IDE\nAugment / Cursor / Claude"])
    MCP["⚙️ Synatyx"]
    LLM(["🤖 LLM"])

    IDE -->|MCP stdio| MCP
    MCP -->|relevant context injected| LLM

    subgraph Memory ["4-Layer Memory"]
        L1["🔴 Working Memory"]
        L2["🟠 Episodic Summaries"]
        L3["🟡 Semantic Knowledge"]
        L4["🟢 Permanent Rules"]
    end

    MCP <-->|read / write| Memory
```

Your AI now **remembers** what you decided last week, **recalls** how your codebase is structured, and **follows** your preferences without being told again.

---

## Why Synatyx

**🧠 Persistent memory across sessions**
Store facts, decisions, and context once — retrieve them forever. No more repeating yourself.

**🎯 Relevance-ranked retrieval**
Hybrid dense + BM25 + MMR pipeline surfaces the right memories, not just the newest ones.

**📦 Multi-project isolation**
Each project gets its own memory space. Switch projects, switch context — nothing bleeds over.

**🔖 Checkpoints that never disappear**
Pin critical decisions as named snapshots. Deprecate when superseded — never permanently deleted.

**✅ Persistent task tracking**
Tasks survive across sessions. Your AI picks up where it left off.

**🤖 Agent skill registry**
Store, index, and RAG-search agent skill definitions. The right agent for the right task, automatically.

**🏭 Production-ready**
Docker Compose, Alembic migrations, health checks, audit log — ready to deploy.

---

## Works With

| Client | Integration |
|--------|------------|
| **Augment Code** | MCP stdio |
| **Cursor** | MCP stdio |
| **Claude Desktop** | MCP stdio |
| **Claude Code** | MCP stdio |
| Any MCP client | JSON-RPC 2.0 / stdio |

---

## Get Started

```bash
git clone https://github.com/tanerincode/synatyx.git && cd synatyx
cp .env.example .env   # add your EMBEDDING_OPENAI_API_KEY
make                   # starts everything + tails logs
```

→ **[Full Setup Guide](docs/local-setup.md)**

---

## Documentation

| Doc | What's inside |
|-----|--------------|
| [Local Setup](docs/local-setup.md) | Prerequisites, Docker, IDE config, Makefile reference, troubleshooting |
| [MCP Tools Reference](docs/mcp-tools.md) | All 18 tools — params, descriptions, examples |
| [Architecture](docs/architecture.md) | 4-layer memory model, retrieval pipeline, tech stack, project structure |

---

## License

MIT © [Taner Tombas](https://github.com/tanerincode)

