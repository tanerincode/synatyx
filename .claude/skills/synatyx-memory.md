---
name: synatyx-memory
description: Long-term memory and context persistence for Claude using the Synatyx context engine. Use when the user asks to remember something, recall past decisions, store project facts, manage tasks, or retrieve context from previous sessions. Activates automatically when working on projects that benefit from persistent memory across conversations.
---

# Synatyx Memory Skill

Synatyx is a long-term memory engine that persists context across conversations using 4 memory layers, vector search, and task tracking. All tools are available via MCP.

## Memory Layers

| Layer | Purpose | Importance |
|-------|---------|------------|
| L1 | Transient / session scratch notes | 0.1–0.4 |
| L2 | Episodic — what happened in this conversation | 0.4–0.6 |
| L3 | Semantic — stable facts, architecture, tech stack | 0.6–0.9 |
| L4 | Procedural — user preferences, coding style, workflow rules | 0.7–1.0 |

## Project Namespacing

Always set `session_id` to the project slug (e.g. `"taty-v2"`) for project-specific operations. This scopes Qdrant retrieval to that project only and prevents cross-project contamination.

- Project facts → `session_id = "<project-slug>"`
- Global / cross-project facts → omit `session_id` or use a descriptive slug (e.g. `"user-preferences"`)

## Workflow

### At the start of every conversation
1. Call `context_retrieve` with the user's first message as the query and `session_id` set to the active project slug
2. Call `context_task_list` to surface pending work
3. Inject retrieved context into your reasoning before responding

### During the conversation
- Call `context_store` whenever a decision, preference, or fact is established
- Call `context_checkpoint` for major milestones or architectural decisions
- Call `context_task_add` when the user mentions future work
- Call `context_task_update` when tasks are completed or cancelled

### At the end of a long session
- Call `context_summarize` to compress the session into L2

## Tool Reference

### `context_retrieve` — Search memory
```
Required: query (str), user_id (str)
Optional: session_id, project, top_k (default 10), memory_layers (["L1","L2","L3","L4"])
```
Use `top_k=5` for focused queries, `top_k=10` for broad topic searches.

### `context_store` — Save a fact
```
Required: content (str), user_id (str), memory_layer (L1|L2|L3|L4)
Optional: session_id, importance (0.0–1.0), confidence, metadata
```
Use `importance=0.9+` for architectural decisions, `0.5–0.7` for useful facts, `0.3` for minor details.

### `context_summarize` — Compress session memory
```
Required: session_id (str), user_id (str)
Optional: max_tokens (default 500), focus (str)
```

### `context_score` — Re-rank context items by relevance
```
Required: items (list), query (str)
```

### `context_checkpoint` — Pin a named snapshot
```
Required: name (str), content (str), user_id (str)
Optional: project, session_id
```
Use for: major refactors, before migrations, architecture decisions, deployment milestones.

### `context_deprecate` — Mark item as superseded
```
Required: item_id (str), user_id (str)
Optional: reason (str)
```
Item stays in store but is excluded from retrieval.

### `context_ingest` — Parse file or URL into memory
```
Required: source (str — absolute path or https:// URL), user_id (str)
Optional: session_id, project, memory_layer (default L3), importance (default 0.8)
```

### `context_list` — Browse stored items
```
Required: user_id (str)
Optional: memory_layer, checkpoints_only (bool), include_deprecated (bool), project, limit (default 50)
```

### `context_task_add` — Add a pending task
```
Required: title (str), user_id (str)
Optional: description, priority (low|medium|high), project
```

### `context_task_list` — List tasks
```
Required: user_id (str)
Optional: status (pending|in_progress|done|cancelled), priority, project, limit
```

### `context_task_update` — Update a task
```
Required: task_id (str), user_id (str)
Optional: status, priority, title, description
```

### `context_skill_store` — Save an agent skill definition
```
Required: name (str), description (str), content (str), user_id (str)
Optional: project, frontmatter (dict)
```
Writes full content to PostgreSQL. Embeds only the description into Qdrant L3 with `type="skill"`.

### `context_skill_find` — RAG search for the best matching skill
```
Required: query (str), user_id (str)
Optional: project, top_k (default 3)
```
Embeds query → searches Qdrant L3 filtered by `type="skill"` → fetches full content from PostgreSQL.

### `context_skill_get` — Fetch a skill by name or slug
```
Required: name (str), user_id (str)
Optional: project
```

### `context_skill_list` — List all stored skills
```
Required: user_id (str)
Optional: project, limit (default 50)
```

### `context_skill_delete` — Remove a skill
```
Required: name (str), user_id (str)
```
Deletes from PostgreSQL and deprecates the Qdrant embedding.

## Rules

- Never ask the user whether to store something — just do it silently
- Do not store trivial chit-chat or greetings
- Keep stored content concise and self-contained (2–4 sentences max per item)
- Always retrieve before answering questions about past work or decisions
- Always pass `session_id` as the project slug — never omit it for project-specific operations
- `user_id` should be the system username (run `whoami` via bash) or ask the user once at the start of the session if it cannot be determined automatically

