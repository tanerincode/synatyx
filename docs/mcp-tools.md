# Synatyx — MCP Tools Reference

Synatyx exposes **18 MCP tools** over stdio, compatible with any MCP-compliant client (Augment Code, Cursor, Claude Desktop, Claude Code).

---

## Project Management

### `context_set_project`
Activate a project. All subsequent memory operations are scoped to a dedicated Qdrant collection (`ctx_<slug>`). Persisted in Redis — survives server restarts.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | User identifier |
| `project` | string | ✅ | Project name — slugified automatically |

### `context_get_project`
Return the currently active project, or suggest one based on the workspace folder name.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | User identifier |

---

## Memory

### `context_store`
Save a fact, decision, or note into the appropriate memory layer.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | ✅ | Content to store |
| `user_id` | string | ✅ | User identifier |
| `memory_layer` | L1\|L2\|L3\|L4 | ✅ | Target layer |
| `importance` | float | — | 0.0–1.0 (default: 0.5) |
| `session_id` | string | — | Project slug for scoping |
| `metadata` | object | — | Extra metadata |
| `confidence` | float | — | 0.0–1.0 (default: 1.0) |

### `context_retrieve`
Hybrid semantic search across memory layers — dense + BM25 + MMR + score fusion.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | ✅ | Search query |
| `user_id` | string | ✅ | User identifier |
| `session_id` | string | — | Project slug to scope results |
| `project` | string | — | Qdrant-level project filter |
| `top_k` | integer | — | Max results (default: 10) |
| `memory_layers` | array | — | Filter to specific layers (default: all) |

### `context_summarize`
Compress session working memory into an L2 episodic summary via LLM. Runs async.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | ✅ | Session to summarize |
| `user_id` | string | ✅ | User identifier |
| `max_tokens` | integer | — | Summary length cap (default: 500) |
| `focus` | string | — | What to focus on |

### `context_score`
Re-rank a list of context items by relevance to a query.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `items` | array | ✅ | Context items to score |
| `query` | string | ✅ | Query to score against |

---

## Knowledge

### `context_checkpoint`
Save a named, pinned L3 snapshot with importance=1.0. Never excluded from retrieval.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Checkpoint name |
| `content` | string | ✅ | What to snapshot |
| `user_id` | string | ✅ | User identifier |
| `project` | string | — | Project scope |
| `session_id` | string | — | Project slug |

### `context_deprecate`
Mark an item as superseded. It stays in the store but is excluded from retrieval.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | string | ✅ | ID of item to deprecate |
| `user_id` | string | ✅ | User identifier |
| `reason` | string | — | Why it's deprecated |

### `context_list`
Browse stored items without vector search — for reviewing checkpoints or finding items to deprecate.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | User identifier |
| `memory_layer` | L1\|L2\|L3\|L4 | — | Filter by layer |
| `checkpoints_only` | boolean | — | Return only checkpoints |
| `include_deprecated` | boolean | — | Include deprecated items |
| `project` | string | — | Filter by project |
| `limit` | integer | — | Max results (default: 50) |

### `context_ingest`
Parse any file or URL into chunks and store them automatically.

Supports: `.docx`, `.pdf`, `.md`, `.py`, `.ts`, `.go`, `.rs`, and any `http(s)://` URL.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | ✅ | Absolute file path or URL |
| `user_id` | string | ✅ | User identifier |
| `memory_layer` | L1\|L2\|L3\|L4 | — | Target layer (default: L3) |
| `importance` | float | — | 0.0–1.0 (default: 0.8) |
| `project` | string | — | Project tag |
| `session_id` | string | — | Project slug |

---

## Tasks

### `context_task_add`
Add a persistent task that survives across sessions.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | ✅ | Short task title |
| `user_id` | string | ✅ | User identifier |
| `description` | string | — | Detailed description |
| `priority` | low\|medium\|high | — | Priority (default: medium) |
| `project` | string | — | Project scope |

### `context_task_list`
List tasks, optionally filtered by status, priority, or project.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | User identifier |
| `status` | pending\|in_progress\|done\|cancelled | — | Filter by status |
| `priority` | low\|medium\|high | — | Filter by priority |
| `project` | string | — | Filter by project |
| `limit` | integer | — | Max results (default: 50) |

### `context_task_update`
Update a task's status, priority, title, or description.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | ✅ | Task ID |
| `user_id` | string | ✅ | User identifier |
| `status` | pending\|in_progress\|done\|cancelled | — | New status |
| `priority` | low\|medium\|high | — | New priority |
| `title` | string | — | Updated title |
| `description` | string | — | Updated description |

---

## Skills

Skills are named agent role definitions (system prompt + capabilities) stored in PostgreSQL and indexed in Qdrant for RAG-based discovery.

### `context_skill_store`
Save a skill definition. Writes full content to PostgreSQL and embeds only the description into Qdrant L3.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Skill name (e.g. `nodejs-developer`) |
| `description` | string | ✅ | One-line description for RAG matching |
| `content` | string | ✅ | Full skill content (system prompt + instructions) |
| `user_id` | string | ✅ | User identifier |
| `project` | string | — | Project scope (null = global) |
| `frontmatter` | object | — | Parsed YAML frontmatter fields |

### `context_skill_find`
RAG search — embed query → search Qdrant L3 (type=skill) → fetch full content from PostgreSQL.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | ✅ | Task description to match |
| `user_id` | string | ✅ | User identifier |
| `project` | string | — | Limit to a specific project |
| `top_k` | integer | — | Max results (default: 3) |

### `context_skill_get`
Fetch a skill by exact name or slug from PostgreSQL.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Skill name or slug |
| `user_id` | string | ✅ | User identifier |
| `project` | string | — | Project scope filter |

### `context_skill_list`
List all stored skills for a user, optionally scoped to a project.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | User identifier |
| `project` | string | — | Filter by project |
| `limit` | integer | — | Max results (default: 50) |

### `context_skill_delete`
Remove a skill from PostgreSQL and deprecate its Qdrant embedding.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | ✅ | Skill name or slug |
| `user_id` | string | ✅ | User identifier |

---

## Garbage Collection

### `context_gc_stats`
Return GC statistics for the active project — how many items are expiring soon, already deprecated, pending hard delete, or protected from GC.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | ✅ | User identifier |

**Response:**
```json
{
  "total_items": 1240,
  "protected": 310,
  "expiring_soon_14d": 42,
  "already_deprecated": 18,
  "pending_hard_delete": 6,
  "gc_enabled": true,
  "l2_base_ttl_days": 30,
  "l3_base_ttl_days": 90,
  "grace_period_days": 30
}
```

> The GC daemon runs as a separate Docker service (`synatyx-gc`). It does not need to be triggered manually — it runs on a configurable interval (default: 24h). Use `context_gc_stats` to monitor its state.

