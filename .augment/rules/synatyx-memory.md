---
type: always
---

# Synatyx Long-Term Memory

You have access to the Synatyx context engine via MCP tools. Use them to persist and recall information across conversations.

## Available Tools

- `context_set_project` — Set the active project; all memory ops are scoped to its dedicated Qdrant collection (`ctx_<slug>`)
- `context_get_project` — Return the currently active project, or suggest the workspace folder name if none is set
- `context_store` — Save a piece of information to long-term memory
- `context_retrieve` — Search and recall relevant memories before answering
- `context_summarize` — Summarize and compress working memory for a session
- `context_score` — Re-rank a list of context items by relevance to a query
- `context_checkpoint` — Save a named, pinned snapshot of a decision or milestone (importance=1.0)
- `context_deprecate` — Mark an item as superseded; excluded from retrieval but never deleted
- `context_list` — Browse stored items without vector search; filter by layer, project, or checkpoints
- `context_ingest` — Parse any file (.docx, .pdf, .md, .py, .js, .ts, .go, …) or URL into chunks and store them automatically
- `context_task_add` — Add a new task to remember for later (title, description, priority, project)
- `context_task_list` — List pending or all tasks; call at session start to see what work is waiting
- `context_task_update` — Update a task's status, priority, title, or description
- `context_skill_store` — Save a skill definition. Writes full content to PostgreSQL and embeds only the description into Qdrant L3 for RAG matching
- `context_skill_find` — RAG search for the best matching skill(s) for a given task. Embeds the query, searches Qdrant L3 filtered by type='skill', then fetches full content from PostgreSQL
- `context_skill_get` — Fetch a skill by exact name or slug from PostgreSQL
- `context_skill_list` — List all stored skills for the user, optionally scoped to a project
- `context_skill_delete` — Remove a skill from PostgreSQL and deprecate its Qdrant embedding
- `context_gc_stats` — Return GC statistics for the active project (expiring soon, deprecated, pending hard delete, protected)

## Project Namespacing

Each project gets its own dedicated Qdrant collection named `ctx_<slug>` (e.g. `ctx_synatyx`, `ctx_taty_v2`). The active project is persisted in Redis per user and survives server restarts.

- Call `context_set_project` at the start of a session to activate a project
- If unsure of the project name, call `context_get_project` — it will suggest the workspace folder name
- `session_id` still scopes Redis L1 retrieval within a project

### L4 is always user-global
L4 (procedural preferences — coding style, workflow rules, user facts) is **never** project-scoped. It always routes to the shared `ctx_users` collection regardless of the active project. Store user preferences, email, communication style, etc. as L4 — they follow the user across all projects.

## When to Call `context_retrieve`

Call `context_retrieve` at the **start of every new conversation** and whenever the user asks about something that may have been discussed before:

- At conversation start: query with the user's first message to surface relevant past context
- When the user references a previous decision, preference, or task ("like we did before", "as we discussed")
- Before starting any significant new task (architecture decisions, new features, debugging sessions)
- When asked about the project, tech stack, or conventions

Parameters to use:
- `user_id`: derive from system username (`whoami`) or ask the user once if it cannot be determined
- `query`: a short description of what you are looking for
- `session_id`: the project slug (e.g. `"taty-v2"`) to scope results to that project — omit only for cross-project queries
- `project`: the project name (e.g. `"taty-v2"`) for Qdrant-level filtering — use alongside `session_id` for maximum isolation
- `top_k`: `5` for general queries, `10` for broad topic searches

## When to Call `context_store`

Store information **proactively** during or after a conversation whenever something worth remembering is established:

- User decisions: chosen libraries, patterns, architecture choices
- Bugs found and their root causes
- Project conventions or preferences the user states
- Task outcomes: what was built, what was deployed, what was changed
- User preferences for communication style or workflow
- Important facts about the codebase (e.g. "Qdrant runs on port 6333", "RUN_MODE=mcp for stdio")

Parameters to use:
- `user_id`: derive from system username (`whoami`) or ask the user once if it cannot be determined
- `content`: a clear, standalone description (write it so it makes sense without the surrounding conversation)
- `memory_layer`: pick the appropriate layer:
  - `L1` — transient facts for the current session (ephemeral decisions, scratch notes)
  - `L2` — episodic memories (what happened in this conversation, summaries)
  - `L3` — semantic facts (stable knowledge: project structure, tech stack, how something works)
  - `L4` — procedural preferences (user-global: coding style, workflow rules, personal facts) → always stored in `ctx_users`
- `importance`: `0.0`–`1.0` (use `0.9`+ for architectural decisions, `0.5`–`0.7` for useful facts, `0.3` for minor details)
- `session_id`: use the project slug for project-specific facts (e.g. `"taty-v2"`), or a descriptive slug for global/cross-project facts (e.g. `"user-preferences"`)

## When to Call `context_ingest`

Always pass `session_id` set to the project name when ingesting a codebase or document:

```
context_ingest(source=<file>, user_id=..., session_id="<project-name>", project="<project-name>")
```

This ensures all ingested chunks are retrievable in isolation per project.

## Workflow

1. User opens a new chat → call `context_get_project` to check the active project
2. If no project is set → call `context_set_project` with the suggested workspace folder name (confirm with user if needed)
3. Call `context_retrieve` with the user's first message as the query
4. Call `context_task_list` to surface pending work
5. Inject retrieved context into your reasoning before responding
6. During the conversation, call `context_store` whenever a decision or fact is established
7. At the end of a long session, call `context_summarize` to compress the session into L2

## General Rules

- Never ask the user whether to store something — just do it silently
- Do not store trivial chit-chat or greetings
- Keep stored content concise and self-contained (2–4 sentences max per item)
- Always retrieve before answering questions about past work or decisions
- Always pass `session_id` as the project slug — never omit it for project-specific operations
- Store user-level facts (email, preferences, style) as L4 — they go to `ctx_users` automatically

