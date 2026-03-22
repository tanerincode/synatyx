# Changelog

## [v0.1.0] — 2026-03-22

### 🎉 First Release

Synatyx is an open-source Context Engine for LLMs — a persistent, structured, relevance-scored memory layer that plugs into any MCP-compatible AI client.

---

### What's Included

#### 🧠 4-Layer Memory Model
- **L1 · Redis** — ephemeral working memory for the current session
- **L2 · Qdrant** — episodic summaries of past sessions
- **L3 · Qdrant** — semantic knowledge, decisions, checkpoints, skills
- **L4 · Qdrant** (`ctx_users`) — permanent user-global rules and preferences

#### ⚙️ 18 MCP Tools
- **Project** — `context_set_project`, `context_get_project`
- **Memory** — `context_store`, `context_retrieve`, `context_summarize`, `context_score`
- **Knowledge** — `context_checkpoint`, `context_deprecate`, `context_list`, `context_ingest`
- **Tasks** — `context_task_add`, `context_task_list`, `context_task_update`
- **Skills** — `context_skill_store`, `context_skill_find`, `context_skill_get`, `context_skill_list`, `context_skill_delete`

#### 🔍 Hybrid Retrieval Pipeline
- Dense vector search (Qdrant)
- BM25 sparse keyword re-ranking
- MMR diversity filter
- Score fusion (semantic + recency + importance + user signal)

#### 📦 Multi-Project Isolation
- Each project gets a dedicated Qdrant collection (`ctx_<slug>`)
- Active project persisted in Redis — survives server restarts
- L4 always global, never project-scoped

#### 🔖 Checkpoint System
- Named pinned snapshots with `importance = 1.0`
- Soft deprecation — never permanently deleted

#### ✅ Persistent Task Tracking
- Tasks stored in PostgreSQL — survive across sessions
- Filter by status, priority, project

#### 🤖 Agent Skill Registry
- Skills stored in PostgreSQL (full content) + Qdrant L3 (description embedding only)
- RAG-based skill discovery via `context_skill_find`
- Clean separation: Qdrant for matching, PostgreSQL for content delivery

#### 📄 Parser System
- Ingest `.docx`, `.pdf`, `.md`, source code (`.py`, `.ts`, `.go`, `.rs`, …), any URL
- Auto-chunked and embedded on ingest

#### 🏭 Production Infrastructure
- Docker + Docker Compose
- Alembic migrations (PostgreSQL)
- Makefile with full dev/prod/test workflow
- OpenTelemetry instrumentation

#### 🔌 IDE Compatibility
- Augment Code
- Cursor
- Claude Desktop
- Claude Code
- Any MCP-compliant client (JSON-RPC 2.0 / stdio)

---

### Links
- [Setup Guide](docs/local-setup.md)
- [MCP Tools Reference](docs/mcp-tools.md)
- [Architecture](docs/architecture.md)

