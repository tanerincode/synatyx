# Changelog

## [Unreleased]

### Handoffs

Briefs between agents now live in Synatyx as versioned "handoffs".

- Six tools: `context_handoff_create`, `context_handoff_get`, `context_handoff_list`, `context_handoff_update`, `context_handoff_report`, `context_handoff_mine`. List and mine return metadata only; `get` returns the body and the latest 10 reports
- `context_handoff_create` takes an optional `created_by` and `context_handoff_update` an optional `author` (default: the user id), recorded on the new version
- Statuses: draft, assigned, in_progress, blocked, done, cancelled. A body change creates a new version
- Tasks gain an `assignee` (stored, filterable, settable on update)
- `context_brief` takes an optional `assignee` and then adds an `open_handoffs` section (assigned, in_progress, blocked; no body) within its own 5% budget share; without it the output is unchanged
- Migration: new `handoffs`, `handoff_versions` and `handoff_reports` tables and `tasks.assignee`; run `alembic upgrade head`

## [v0.2.0] — 2026-08-03

### 📦 Context Engine

From memory server to context engine: Synatyx now assembles context, not just stores it.

#### `context_pack` — task-driven context assembly
- One call: the task you're about to do + a token budget → a prompt-ready block of relevant memories (+1-hop relations), pinned checkpoints, dead-end attempts, open tasks, matching skills, and code-index hits
- Unified section budgeting (`SectionBudgeter`): weight renormalization for absent sections, spillover reallocation of unspent budget
- `rendered` markdown carries provenance (`[user-stated]`) and staleness (`[STALE: …]`) markers

#### 🗂️ Persistent code & doc index — `context_index`, `context_index_search`, `context_index_status`
- Per-project `ctx_<slug>__index` collection, isolated from memories by construction (GC, consolidation, observer, and dashboard skip it)
- Idempotent indexing: deterministic uuid5 point ids, whole-file hash skip, chunk-level incremental re-embedding, stale-chunk sweep, vanished-file cleanup
- `.gitignore`-aware directory walking (`git ls-files`), binary/lockfile/oversize skips
- Hybrid search: dense + exact-symbol + full-text passes fused with BM25 — exact identifiers hit even when embeddings miss
- Verbatim code storage: bypasses the memory sanitizer and 600-char re-chunking

#### 🔌 Proactive context — MCP resources & prompts
- Resources: `context://brief`, `context://projects`
- Prompts: `session-start`, `pack-context` (arguments supported)
- Identity via `DEFAULT_USER_ID` (falls back to OS user)

#### 🚀 Streamable-HTTP transport
- Modern endpoint at `/mcp` with `stateless_http=True` — deploy restarts no longer strand clients on dead session ids (the SSE `-32602` problem)
- Legacy SSE stays mounted at `/mcp/sse` (deprecated)

#### 🤖 Automatic indexing
- **Push indexing** for repos the server can't read: `POST /index/diff` (hash manifest → what changed) + `POST /index/files` (upload changed content, prune deletions), driven by stdlib-only `scripts/index_project.py` — hook-safe for git post-commit / Claude Code SessionStart / cron
- **Watch roots** for mounted code: `INDEX_WATCH_ROOTS` makes the GC daemon discover and re-index projects on an interval (git repo root = one project; workspace dir = one project per subdir)
- Server build info surfaced on `/health` and the dashboard header (`v0.2.0 · 31 tools · <commit>`); `GIT_COMMIT` baked in at Docker build

#### 🧰 Fixes & maintenance
- Qdrant payload indexes on every hot filter field (previously all filters were unindexed)
- `search()` now restores `created_at`, making the recency scoring signal real
- `CodeParser` no longer duplicates nested defs; emits `kind` + `line_end`
- `Makefile` `run`/`logs` fixed (referenced a nonexistent compose service)
- `docs/mcp-tools.md` is now generated from `tools.json` (`scripts/gen_tool_docs.py`)
- 31 MCP tools total

---

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

