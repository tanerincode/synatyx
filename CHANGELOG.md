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

#### ⚙️ 19 MCP Tools
- **Project** — `context_set_project`, `context_get_project`
- **Memory** — `context_store`, `context_retrieve`, `context_summarize`, `context_score`
- **Knowledge** — `context_checkpoint`, `context_deprecate`, `context_list`, `context_ingest`
- **Tasks** — `context_task_add`, `context_task_list`, `context_task_update`
- **Skills** — `context_skill_store`, `context_skill_find`, `context_skill_get`, `context_skill_list`, `context_skill_delete`
- **GC** — `context_gc_stats`

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

#### 🗑️ Garbage Collection — Forgetting System
- Separate `synatyx-gc` Docker Compose service (`RUN_MODE=gc`)
- Importance-weighted TTL: `effective_ttl = base_ttl × (1 + importance × 3.0)`
- L2 episodic: 30-day base TTL · L3 semantic: 90-day base TTL
- Two-phase deletion: soft deprecation first → hard delete after 30-day grace period
- Fire-and-forget `last_accessed_at` tracking on every retrieval hit — items that stay relevant never expire
- Immune items never auto-expire: checkpoints (`is_pinned`), L4 preferences, skills, `importance=1.0`
- Full audit log in PostgreSQL `gc_log` table (run_id, item_id, collection, action, reason)
- `context_gc_stats` MCP tool for live monitoring

#### 🏭 Production Infrastructure
- Docker + Docker Compose (4 services: `synatyx`, `synatyx-gc`, `qdrant`, `postgres`)
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

