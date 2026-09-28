# Persistent Code & Doc Index

Session-only code indexes die with the session. Synatyx owns a **persistent**
per-project index that survives restarts, feeds `context_pack`, and is searchable
with exact-symbol precision.

## Where it lives

Each project gets a sibling Qdrant collection: `ctx_<slug>__index`. It is
isolated from memories **by construction** — GC decay, consolidation, the
relation observer, briefs, and the dashboard all skip `__index` collections, so
code chunks can never leak into memory semantics.

## Tools

### `context_index` — build / refresh

```
context_index(source="/path/to/repo", user_id="taner", project="my-app")
```

- **Idempotent**: point ids are `uuid5(project:path:chunk_index)`; re-running
  is safe. Unchanged files (whole-file hash) are skipped; changed files
  re-embed only the chunks whose content hash moved; shrunken files get their
  tail chunks swept; files deleted from disk are removed on directory re-index.
- **.gitignore-aware**: in a git repo the file list comes from
  `git ls-files --cached --others --exclude-standard`; otherwise a static
  exclude list (`node_modules`, `.venv`, `dist`, …). Binary files, lockfiles,
  and files over 200 KB are skipped.
- **Verbatim code**: the index path bypasses the memory store's sanitizer and
  600-char re-chunking. Chunks are parser-shaped (function/class/method for
  Python, heading for markdown) and only re-split above ~1600 chars.

### `context_index_search` — hybrid search

```
context_index_search(query="where is `get_storage_for` defined", user_id="taner")
```

Three passes fused into one ranking:

1. **Dense** — semantic kNN over chunk embeddings
2. **Exact symbol** — keyword-indexed `symbol` payload lookup (function/class/
   method names). Backtick a term in the query to force it.
3. **Full text** — Qdrant `MatchText` over chunk content

Fusion: `0.65·dense + 0.35·BM25` over the candidate union, `+0.25` exact-symbol
boost, `+0.10` full-text boost. This is what makes identifier queries work:
an exact name dense search misses can still enter the candidate pool by term.

Hits carry `path`, `symbol`, `kind`, line range, a snippet, match flags, and
`possibly_stale: true` when the file changed on disk since indexing.

### `context_index_status` — what's indexed

File/chunk counts, per-language breakdown, last index time, plus `stale_files`
(changed on disk) and `missing_files` (deleted) so you know when to re-run
`context_index`.

## Automatic indexing

Two mechanisms keep indexes fresh without anyone calling `context_index`. Both
write only to `ctx_<slug>__index` — memories and code stay separated by
construction.

### Push from a service — `context_index_push` / `context_index_remove`

A service with its own checkout (CI, the review service's indexer) pushes file
contents directly: `context_index_push {project, user_id, files: [{path,
content}], force?}` and `context_index_remove {project, user_id, paths}`, or
the same over REST as `POST` / `DELETE /v1/projects/{slug}/index`. The caller
works out what changed (e.g. `git diff --name-status last..head`), so no
manifest round trip. `project` is required — a push never follows the
active-project pointer. Limits per call: 50 files and 4 MiB, or 1000 paths.

### Push indexing — when the code is NOT on the server

The server can't read your laptop, so the client does the walking:

```bash
export SYNATYX_URL=https://context.example.com SYNATYX_AUTH_KEY=...
python scripts/index_project.py --root ~/workspace/my-app --project my-app
```

The script hashes every indexable file locally, asks `POST /index/diff` what
changed, uploads only those files to `POST /index/files` (batched), and prunes
deletions. An unchanged repo costs one small request. It always exits 0, so
it's safe to wire anywhere:

- **git post-commit hook** (`.git/hooks/post-commit`):
  ```bash
  #!/bin/sh
  python /path/to/synatyx/scripts/index_project.py --root "$(git rev-parse --show-toplevel)" &
  ```
- **Claude Code SessionStart hook** (`.claude/settings.json`):
  ```json
  { "hooks": { "SessionStart": [ { "hooks": [ { "type": "command",
    "command": "python /path/to/synatyx/scripts/index_project.py" } ] } ] } }
  ```

### Watch roots — when the code IS on the server

Set `INDEX_WATCH_ROOTS` and the GC daemon discovers and re-indexes projects on
an interval (default 6h):

```
INDEX_WATCH_ROOTS=/workspace          # each subdir = one project
INDEX_WATCH_ROOTS=/srv/repos/my-app   # a git repo root = one project
```

Idempotent per pass — unchanged files cost one hash each. Works great with a
read-only volume mount in docker-compose.

## How `context_pack` uses it

When a project has an index, `context_pack` adds a `code` section (14% of the
budget by default) with the top index hits for the task query — snippets,
locations, and staleness flags — alongside memories, tasks, and skills.
