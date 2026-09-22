# MCP Tools Reference

Synatyx exposes **32 MCP tools**. This file is generated from `src/transports/mcp/tools.json` by `scripts/gen_tool_docs.py` — edit the JSON, then regenerate.

## Context Assembly

### `context_brief`

One-call session-start digest — call this FIRST in every new conversation instead of separate get_project/retrieve/task_list calls. Returns a token-budgeted briefing: identity (L4 user preferences), last_session (recent L2 summaries), project_knowledge (pinned checkpoints + top L3 facts), recent_changes (items stored in the last N days), recent_attempts (tried-and-failed records so you don't repeat dead ends), open_tasks, and stats (item counts by layer).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `session_id` | string | no | Project slug — when no explicit project is passed, this scopes the WHOLE brief (collection routing, memories, and open_tasks) to that project |
| `project` | string | no | Project slug — scopes the whole brief to this project only (memories, recent changes, attempts, open_tasks). Falls back to session_id, then the active project (optional) |
| `max_tokens` | integer | no | Token budget for the whole briefing (default: 2000) |
| `recent_days` | integer | no | Window for the recent_changes section in days (default: 7) |

### `context_pack`

Assemble ONE prompt-ready context block for a specific task — query-driven, unlike context_brief's session-start digest. Returns structured sections (identity, relevant memories with 1-hop relations, pinned checkpoints, dead-end attempts, open tasks, matching skills, code-index hits) AND a 'rendered' markdown string ready to inject into a prompt, with provenance and staleness markers. Unspent section budget is reallocated to fuller sections. Call this before starting any significant task — it replaces separate retrieve + task_list + skill_find calls.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | The task or question to pack context for |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug — overrides the active-project pointer for this call (optional) |
| `session_id` | string | no | Session identifier for session scoping (optional) |
| `max_tokens` | integer | no | Token budget for the whole pack (default: 3000) |
| `include_code` | boolean | no | Include code/doc index hits when the project has an index (default: true) |

## Project Management

### `context_set_project`

Set the active project for the current user. All subsequent memory operations (store, retrieve, list, ingest, checkpoint) will be scoped to a dedicated Qdrant collection for this project (e.g. 'synatyx' → collection 'ctx_synatyx'). The selection is persisted in Redis and survives server restarts. If unsure of the project name, call context_get_project first — it will suggest the workspace folder name.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | yes | Project name to activate (e.g. 'synatyx', 'taty-v2'). Will be slugified automatically. |

### `context_get_project`

Return the currently active project for the user. If no project has been set, returns a suggestion based on the current workspace folder name — call context_set_project with the suggested name to confirm.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |

## Memory

### `context_store`

Store content into the appropriate memory layer. Pass either a single 'content' + 'memory_layer', or 'items' to store several facts in one call (preferred when saving multiple facts — one round-trip instead of N). After storing (L2-L4), same-purpose detection runs automatically: near-identical memories are auto-linked with alternative_to edges (auto_linked in the response); probable matches are returned as suggestions to confirm with context_relate (suggestions in the response). To record a failed approach (so future sessions don't repeat it), store an L2 item with metadata {type: 'attempt', goal, approach, outcome: 'failed', why} — context_brief surfaces these in its recent_attempts section.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | no | Content to store (single-item mode) |
| `items` | array | no | Batch mode: list of items to store in one call. Each item: {content, memory_layer, importance?, metadata?, confidence?, session_id?, origin?}. When provided, top-level content/memory_layer are ignored. |
| `user_id` | string | yes | User identifier |
| `importance` | number | no | Importance score 0.0-1.0 |
| `memory_layer` | `L1` \| `L2` \| `L3` \| `L4` | no |  |
| `session_id` | string | no | Project namespace or session identifier. Use the project slug (e.g. 'taty-v2') for project-specific facts so they are retrievable in isolation. Use a descriptive slug (e.g. 'user-preferences') for global or cross-project facts. |
| `metadata` | object | no | Extra metadata (optional). Conventions: files: [paths] — the files this fact refers to; their content hashes are stored so retrieval can flag the memory possibly_stale when they change. fact_type: 'file-location' | 'config' | 'architecture' | 'preference' — controls type-aware TTL decay in GC (file locations expire fast, preferences slowly). type: 'attempt' marks a tried-and-failed record (see description). |
| `confidence` | number | no | Confidence score 0.0-1.0 (default: 1.0) |
| `origin` | `user-stated` \| `agent-inferred` \| `ingested-from-file` \| `ingested-from-web` \| `web-search` | no | Provenance of this fact: 'user-stated' when the user said it directly, 'agent-inferred' for your own conclusions (default), 'web-search' for facts found online. Ingested content is tagged automatically. Stored in metadata.origin and returned on retrieval so trust level is always visible. |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_retrieve`

Retrieve relevant context items for the current query from all memory layers.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Current question or topic |
| `user_id` | string | yes | User identifier |
| `session_id` | string | no | Project namespace or session identifier. Pass the project slug (e.g. 'taty-v2') to scope retrieval to that project only and avoid cross-project contamination. Omit only for cross-project or global queries. |
| `project` | string | no | Project name to filter results by (e.g. 'taty-v2'). Filters at the Qdrant level for efficient project-scoped retrieval. Can be used alongside or instead of session_id. |
| `top_k` | integer | no | Max items to return (default: 10) |
| `memory_layers` | array | no | Which memory layers to query (default: all) |
| `expand_relations` | boolean | no | Also include memories linked to the retrieved items via relations (1-hop, marked with via_relation). Default: false |
| `filters` | object | no | Metadata equality filters pushed down to the vector store's payload filter, e.g. `{"locale": "en", "source_id": "help-42"}`. Applied before `top_k`, not after, so a filtered search still returns up to `top_k` items. |

Each returned item carries a `citation` object — `sourceId`, `url`, `title`,
`offsets {start, end}` — with every key present and `null` when unknown, so an
answer can attribute the passage it used.

### `context_summarize`

Summarize the working memory for a session. Runs async and off the critical
path by default; pass `sync: true` to get the summary back in the same call.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | yes | Session to summarize |
| `user_id` | string | yes | User identifier |
| `max_tokens` | integer | no | Max summary length in tokens (default: 500) |
| `focus` | string | no | What to focus on in the summary (optional) |
| `sync` | boolean | no | Wait for the summary and return `{summary, keyEntities, tokensSaved}` in this call. Default false. |

An agent compacting its own context in the background has nothing to wait for,
which is why the default schedules the work. A caller assembling a prompt *now*
needs the summary itself, and a scheduled call hands it nothing.

### `context_score`

Score a list of context items by relevance to a query.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `items` | array | yes | List of context items to score |
| `query` | string | yes | Query to score against |

## Code & Doc Index

### `context_index`

Index a file, directory, or glob into the project's persistent code/doc index (a separate ctx_<slug>__index collection — never mixed into memories). Idempotent: unchanged files are skipped via content hashes, changed files re-embed only their changed chunks, and stale chunks are swept. Respects .gitignore in git repos. Requires an active or explicit project.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | yes | Absolute path to a file or directory, or a glob pattern |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug (optional — defaults to the active project; required if none is set) |
| `force` | boolean | no | Re-embed files even if their hash is unchanged (default: false) |
| `max_files` | integer | no | Cap on files per call (default: 500) |

### `context_index_search`

Hybrid search over the project's persistent code/doc index: dense semantic search fused with exact-symbol and full-text keyword matching, so exact identifiers (function/class names) are found even when semantic search misses them. Returns snippets with path, symbol, and line numbers, flagged possibly_stale when the file changed on disk since indexing. Backtick an identifier in the query for an exact-match boost.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Natural-language question or an identifier (backtick exact symbols for a boost) |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug (optional) |
| `top_k` | integer | no | Max hits (default: 5) |
| `language` | string | no | Filter by language, e.g. 'py', 'ts' (optional) |
| `path_prefix` | string | no | Only hits whose path starts with this prefix (optional) |

### `context_index_status`

Report what's in the project's code/doc index: file and chunk counts, per-language breakdown, last index time, and which indexed files are stale (changed) or missing (deleted on disk) since indexing.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug (optional) |
| `check_staleness` | boolean | no | Re-hash indexed files against disk (default: true, capped at 200 files) |

## Knowledge

### `context_checkpoint`

Save a named checkpoint — a pinned, high-importance L3 memory snapshot of a decision, milestone, or code state. Use this when something significant happens that should be permanently remembered and retrievable by name.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Checkpoint name (e.g. 'v1-auth-refactor', 'before-db-migration') |
| `content` | string | yes | What to snapshot — decision rationale, code summary, milestone description |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project name for filtering (optional) |
| `session_id` | string | no | Project namespace or session identifier. Use the project slug (e.g. 'taty-v2') to associate this checkpoint with a specific project. |

### `context_deprecate`

Mark an existing memory item as deprecated. The item is NOT deleted — it stays in the store but is excluded from normal retrieval. Use this when a checkpoint or fact is superseded by a newer one. Pass superseded_by to record which item replaces it (creates a 'supersedes' relation automatically).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | string | yes | ID of the item to deprecate |
| `user_id` | string | yes | User identifier |
| `reason` | string | no | Why this item is being deprecated (optional) |
| `superseded_by` | string | no | ID of the newer item that replaces this one — records a 'supersedes' relation (new item → deprecated item) so replacement history is traceable (optional) |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_deprecate_source`

Deprecate every live item ingested under one source id, and report how many.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `source_id` | string | yes | The source id whose items to retire |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer (optional) |
| `reason` | string | no | Why the source is being retired (optional) |

This is how a document is retired or replaced. A caller never learns the item
ids its document became, so addressing them one by one is not possible. Items
are deprecated, not deleted, so history and supersedes chains stay readable. A
source that is already gone returns `0` — a successful no-op, not an error,
which is what a sync retrying after a timeout needs to see.

### `context_erase_user`

Hard-delete every item belonging to a user id, across every collection.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `target_user_id` | string | yes | The user id to erase |
| `user_id` | string | yes | User identifier of the caller |

For erasure requests, where deprecation is not an answer. Sweeps project
collections, the shared L4 collection, and per-project code indexes, because a
user id can appear in any of them and an erasure that misses one is not an
erasure. **This cannot be undone.** The target is a separate argument from
`user_id` on purpose: erasing someone must be deliberate, never a defaulted
value. Covers the vector store; rows outside it are not touched.

### `context_list`

List memory items without a vector search — for browsing checkpoints, reviewing what's stored, or finding items to deprecate.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `memory_layer` | `L1` \| `L2` \| `L3` \| `L4` | no | Filter by memory layer (optional) |
| `checkpoints_only` | boolean | no | Return only checkpoint items (default: false) |
| `include_deprecated` | boolean | no | Include deprecated items (default: false) |
| `project` | string | no | Filter by project name (optional) |
| `limit` | integer | no | Max items to return (default: 50) |

### `context_ingest`

Parse and ingest a file, a URL, or caller-held text into memory as chunks.
Supports .docx, .pdf, .md, source code files (.py, .js, .ts, .go, .rs, ...), any
http(s):// URL, or raw text in `text`. Each chunk is embedded and stored
automatically.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | no | Absolute file path or URL to ingest. Exactly one of `source` or `text`. |
| `text` | string | no | Content the caller already holds, ingested directly instead of being fetched. Exactly one of `source` or `text`. |
| `user_id` | string | yes | User identifier |
| `source_id` | string | no | The caller's own identifier for this document, stored on every chunk. A source id *is* the document identity — there is no separate document entity — so it is what `context_deprecate_source` retires and what a re-sync replaces. |
| `url` | string | no | Citation URL recorded on every chunk, for text fetched elsewhere |
| `title` | string | no | Document title recorded on every chunk, used when citing it |
| `locale` | string | no | Document locale (e.g. `en`), recorded on every chunk and filterable at retrieval |
| `memory_layer` | `L1` \| `L2` \| `L3` \| `L4` | no | Memory layer to store chunks in (default: L3) |
| `importance` | number | no | Importance score 0.0-1.0 (default: 0.8) |
| `project` | string | no | Project name tag for filtering (optional) |
| `session_id` | string | no | Project namespace. Always set this to the project name (e.g. 'taty-v2') so ingested chunks are scoped to that project and retrievable in isolation without cross-project contamination. |

## Relations & Graph

### `context_relate`

Link two memory items with a typed relation (source → target) so they can be retrieved and handled together. Known types: related_to, supersedes, part_of, depends_on, caused_by — custom types are also accepted.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `source_id` | string | yes | ID of the source memory item |
| `target_id` | string | yes | ID of the target memory item |
| `relation_type` | string | no | Relation type (default: related_to). Known: related_to, supersedes, part_of, depends_on, caused_by. Custom strings allowed. |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project name tag (optional) |
| `metadata` | object | no | Extra metadata for the edge (optional) |

### `context_unrelate`

Remove a relation between two memory items — by relation_id, or by the source_id + target_id pair (optionally narrowed by relation_type).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `relation_id` | string | no | ID of the relation to remove |
| `source_id` | string | no | Source item ID (used with target_id when relation_id is not given) |
| `target_id` | string | no | Target item ID (used with source_id when relation_id is not given) |
| `relation_type` | string | no | Only remove edges of this type (optional, with source_id/target_id) |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_related`

List the memories linked to an item via relations, with the connecting edges. Follows supersedes chains into deprecated items (direct lookup, not search).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | string | yes | Memory item ID whose neighbors to fetch |
| `user_id` | string | yes | User identifier |
| `relation_type` | string | no | Only follow edges of this type (optional) |
| `direction` | `out` \| `in` \| `both` | no | Edge direction relative to item_id (default: both) |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_get`

Fetch a single memory item directly by its ID — no vector search. Works for deprecated items too. Use when you already know the item id (e.g. from a previous store/retrieve/related call).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `item_id` | string | yes | Memory item ID |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_visualize`

Render the memory graph (items + relation edges) as a Mermaid flowchart. Nodes are colored by memory layer (L1-L4), deprecated items are dashed, pinned items get a thick border; edges are labeled with their relation type. The returned mermaid string renders natively in Claude and any Mermaid viewer. Best for up to ~50 nodes.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | no | Filter items by project tag (optional) |
| `memory_layer` | `L1` \| `L2` \| `L3` \| `L4` | no | Only include items from this layer (optional; L4 reads the shared user collection) |
| `relations_only` | boolean | no | Only show items that have at least one relation edge (default: false) |
| `include_deprecated` | boolean | no | Include deprecated items, e.g. supersedes targets (default: true) |
| `direction` | `LR` \| `TD` | no | Graph layout direction: left-to-right or top-down (default: LR) |
| `limit` | integer | no | Max items to include (default: 50) |

### `context_alternatives`

Answer 'what can I use for X?' — semantic search for a purpose (e.g. 'approve button'), returning each matching memory grouped with its alternatives (items linked via alternative_to or used_for edges). Alternatives are detected automatically at store time: near-identical purposes are auto-linked, probable matches are returned as suggested_relations in the store response.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `query` | string | yes | The purpose to search for, e.g. 'approve button component' |
| `top_k` | integer | no | Max groups to return (default: 5) |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

## Tasks

### `context_task_add`

Add a new task to remember for later. Use this when the user mentions something they want to do later, a feature to build, or any pending work item.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Short task title |
| `user_id` | string | yes | User identifier |
| `description` | string | no | Detailed description (optional) |
| `priority` | `low` \| `medium` \| `high` | no | Priority (default: medium) |
| `project` | string | no | Project name for filtering (optional) |

### `context_task_list`

List pending or all tasks. Call this at the start of a session to see what work is waiting.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `status` | `pending` \| `in_progress` \| `done` \| `cancelled` | no | Filter by status (default: pending) |
| `priority` | `low` \| `medium` \| `high` | no | Filter by priority (optional) |
| `project` | string | no | Filter by project (optional) |
| `limit` | integer | no | Max tasks to return (default: 50) |

### `context_task_update`

Update a task — change its status, priority, title, or description.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | yes | Task ID to update |
| `user_id` | string | yes | User identifier |
| `status` | `pending` \| `in_progress` \| `done` \| `cancelled` | no | New status |
| `priority` | `low` \| `medium` \| `high` | no | New priority |
| `title` | string | no | Updated title |
| `description` | string | no | Updated description |

## Skills

### `context_skill_store`

Save a skill definition. Writes full content to PostgreSQL and embeds only the description into Qdrant L3 for RAG matching.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Skill name (e.g. 'nodejs-developer') |
| `description` | string | yes | One-line description used for RAG matching |
| `content` | string | yes | Full skill content (system prompt + instructions) |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project scope (optional, null = global) |
| `frontmatter` | object | no | Parsed YAML frontmatter fields (optional) |

### `context_skill_find`

RAG search for the best matching skill(s) for a given task. Embeds the query, searches Qdrant L3 filtered by type='skill', then fetches full content from PostgreSQL.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | string | yes | Task description to match against stored skills |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Limit search to a specific project (optional) |
| `top_k` | integer | no | Max number of skills to return (default: 3) |

### `context_skill_get`

Fetch a skill by exact name or slug from PostgreSQL.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Skill name or slug to look up |
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project scope filter (optional) |

### `context_skill_list`

List all stored skills for the user, optionally scoped to a project.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | no | Filter by project (optional) |
| `limit` | integer | no | Max skills to return (default: 50) |

### `context_skill_delete`

Remove a skill from PostgreSQL and deprecate its Qdrant embedding.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Skill name or slug to delete |
| `user_id` | string | yes | User identifier |

## Maintenance

### `context_consolidate`

Manually trigger sleep-style memory consolidation for the active project: clusters of similar episodic (L2) memories (cosine >= threshold, cluster size >= minimum) are merged into a single L3 fact; the originals are deprecated (never deleted) and linked to the merged item with supersedes edges. Attempt records, pinned items, and existing consolidations are never merged. The GC daemon also runs this automatically in the background when enabled.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_gc_stats`

Return garbage collection statistics for the active project — how many items are expiring soon, already deprecated, pending hard delete, or protected.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | no | Project slug to operate in — overrides the active-project pointer for this call. Pass it in multi-session setups so concurrent sessions in different projects don't route into each other's collections (optional) |

### `context_usage`

Report token spend metered per tool call: outbound context tokens (what tool responses inject into the agent's context), inbound argument tokens, and embedding-API tokens with estimated USD cost. Returns totals plus a breakdown grouped by tool, project, or day.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_id` | string | yes | User identifier |
| `project` | string | no | Only count calls attributed to this project slug (optional) |
| `days` | integer | no | Time window in days (default: 30) |
| `group_by` | `tool` \| `project` \| `day` | no | Breakdown dimension (default: tool) |
