# REST API (`/v1`)

A plain JSON API over the same engine the MCP tools use, for backend services
rather than agents.

MCP is the right protocol for an AI client and the wrong one for a service: a
backend calling Synatyx over MCP has to speak JSON-RPC, hold a session and
unwrap results out of `TextContent`, all to make what is, on its side, one HTTP
call. These routes are that call. They are a transport, not a second
implementation — every route delegates to the same tools and services, so
behaviour, token metering and session capture cannot drift between the two.

## Auth

Every `/v1` route sits behind the same key as the rest of the server: send
`AUTH_ADMIN_KEY` in the `X-Auth-Key` header (or as `Authorization: Bearer
<key>`). `/v1/health` is included — a readiness probe sends the key like any
other caller.

A key today is a whole-server key. Project-scoped keys, which restrict a caller
to a set of projects and to these routes, are a separate change.

## Conventions

- Request and response fields are camelCase. `snake_case` is also accepted on
  input, so callers porting from the MCP tools do not have to rewrite payloads.
- `project` routes the call to that project's collection and is required on
  every route that touches stored content.
- Routes that do not carry a `userId` store under the server's configured
  default user.
- Times and ids are opaque strings. Do not parse them.

## Errors

Every non-2xx response has the same body:

```json
{ "error": { "code": "INVALID_REQUEST", "message": "...", "details": { } } }
```

Branch on `code`, never on the message.

| Code | Status | Meaning |
| --- | --- | --- |
| `INVALID_REQUEST` | 400 | Malformed body, missing or invalid fields. `details.missing` lists every missing field at once. |
| `NOT_FOUND` | 404 | The thing addressed by id does not exist for this user (a handoff, or a handoff version). |
| `NOT_READY` | 503 | The server is still starting and has no storage connections yet. |
| `UPSTREAM_UNAVAILABLE` | 502 | A backend the call needed — vector store, embedding provider — failed. |
| `INTERNAL_ERROR` | 500 | An unhandled error, or an operation that half-succeeded and the caller needs to know. |

A caller's own mistake never arrives as a 5xx. A tool that rejects an argument
— an unknown memory layer, a missing key — comes back as `400
INVALID_REQUEST`, not as `502`; consumers rightly read any 5xx as "this
dependency is down", and a mistyped field is not worth paging anyone for. Every
response from these routes is an envelope, including an unhandled error, so a
non-envelope 5xx means something in front of the server, not the server.

An empty result is **not** an error. A retrieval against a project with nothing
ingested returns `200` with `chunks: []`, plus a `diagnostics` object explaining
why nothing matched. "Nothing stored yet" and "Synatyx is broken" demand
opposite reactions from a caller, so they never share a status code.

## `POST /v1/ingest`

Ingest one document. Either Synatyx fetches it, or the caller pushes the text.

```json
{
  "project": "cx-lens-ai",
  "sourceId": "help-42",
  "url": "https://help.example.com/cancel",
  "metadata": { "title": "Cancelling", "locale": "en" }
}
```

Exactly one of `url` and `text`. `sourceId` is the caller's own identifier for
the document; it is stored on every chunk, and it is what
`/v1/sources/deprecate` and re-ingestion address later. `metadata` is merged
into every chunk — `title`, `url` and `locale` are what make a chunk citable
when it comes back from retrieval.

```json
{ "sourceId": "help-42", "source": "https://help.example.com/cancel", "chunkCount": 12, "chunksFailed": 0 }
```

There is no separate document id: in this model a source id *is* the document's
identity, one source id per document.

## `POST /v1/retrieve`

```json
{ "project": "cx-lens-ai", "query": "how do I cancel", "topK": 8 }
```

`topK` defaults to 8. Results come back sorted by score, already capped.

```json
{
  "chunks": [
    {
      "chunkId": "…",
      "sourceId": "help-42",
      "text": "You can cancel from Settings…",
      "score": 0.81,
      "url": "https://help.example.com/cancel",
      "title": "Cancelling",
      "offsets": { "start": 0, "end": 240 }
    }
  ]
}
```

`url`, `title` and `offsets` are always present and are `null` when unknown,
rather than omitted — a caller rendering citations needs to tell "no title"
apart from "this server does not send titles". `url` is only ever a real URL; a
chunk ingested from a file path reports `null` rather than offering a path that
would render as a dead link.

## `POST /v1/sources/deprecate`

```json
{ "project": "cx-lens-ai", "sourceId": "help-42" }
```

Deprecates every live chunk from that source and returns how many:

```json
{ "sourceId": "help-42", "deprecated": 12 }
```

Items are deprecated, not deleted, so history stays readable. `deprecated: 0` is
a successful no-op — retiring an already retired source is not an error, which
matters for a sync that retries after a timeout.

## Conversation memory

`POST /v1/memory/store` — `{ project, userId, content, metadata?, conversationId? }`
→ `{ id, ids }`. Stored as L2 unless `memoryLayer` says otherwise.

`POST /v1/memory/retrieve` — `{ project, userId, query, topK? }`
→ `{ items: [{ id, content, score, metadata }] }`. Searches L1 and L2 only;
knowledge lives in L3 and is reached through `/v1/retrieve`.

`POST /v1/memory/summarize` — `{ project, userId, conversationId, maxTokens?, focus? }`
→ `{ summary, keyEntities, tokensSaved }`.

Summarization here is **synchronous**, unlike the `context_summarize` tool,
which schedules the work and returns immediately. An agent compacting its own
context in the background has nothing to wait for; a service assembling a
prompt *now* needs the summary in the same call. An empty `summary` means the
window held nothing to summarize.

## Code index

For a service that keeps a repo's code index current from its own checkout
(the review service's indexer does this per registered repo). Same tools as
`context_index_push` / `context_index_remove`; `slug` is the project.

`POST /v1/projects/{slug}/index` — `{ files: [{ path, content }], force?, userId? }`
→ `{ filesIndexed, filesUnchanged, filesSkipped, filesFailed, chunksUpserted, chunksDeleted, details }`.

`DELETE /v1/projects/{slug}/index` — `{ paths: [...], userId? }`
→ `{ pathsRemoved, chunksDeleted }`.

One call carries at most 50 files and 4 MiB of content, or 1000 paths; a
larger batch is a 400 naming the limit, and nothing from it is indexed. Paths
are repo-relative. Re-pushing an unchanged file re-embeds nothing
(`chunksUpserted: 0`).

Chunks belong to `userId` (the server default when absent), and
`context_index_search` only sees chunks of the user it searches as — so a
service pushes and searches under one id.

## Handoffs

Versioned briefs one agent writes for another. Same tools as
`context_handoff_*`; every route delegates to them. Handoffs belong to
`userId` (the server default when absent). `project` is slugified, so
`Synatyx` and `synatyx` are one project.

Metadata objects look like this. **List and mine never include `body`; only
`GET /v1/handoffs/{id}` does.**

```json
{ "id": "7c1e…", "project": "synatyx", "slug": "rest-routes", "title": "REST routes",
  "assignee": "synatyx-w2", "status": "assigned", "version": 1, "parentId": null,
  "taskIds": [], "createdBy": "synatyx-lead",
  "createdAt": "2026-10-19T12:00:00+00:00", "updatedAt": "2026-10-19T12:00:00+00:00" }
```

`status` is one of `draft`, `assigned`, `in_progress`, `blocked`, `done`,
`cancelled`. Error mapping: unknown id or version → `404 NOT_FOUND`; slug
already used in the project → `409 INVALID_REQUEST`; bad status, missing field,
unknown `parentId` → `400 INVALID_REQUEST`.

### `GET /v1/handoffs?userId&project&assignee&status`

```
GET /v1/handoffs?project=synatyx&assignee=synatyx-w2
→ 200 { "handoffs": [ { …metadata… } ], "count": 1 }
```

### `GET /v1/handoffs/mine?userId&assignee&project`

Open handoffs only (`assigned`, `in_progress`, `blocked`); `assignee` is required.

```
GET /v1/handoffs/mine?assignee=synatyx-w2
→ 200 { "handoffs": [ { …metadata… } ], "count": 1 }
```

### `GET /v1/handoffs/{id}?userId&version`

The full brief plus the 10 newest reports. `version` returns an earlier body.

```
GET /v1/handoffs/7c1e…
→ 200 { "handoff": { …metadata…, "body": "# Brief\n…" },
        "reports": [ { "id": "…", "handoffId": "7c1e…", "agent": "synatyx-w2",
                       "text": "routes done", "statusAfter": "done",
                       "createdAt": "…" } ] }
```

### `POST /v1/handoffs`

`{ project, title, body, assignee?, slug?, parentId?, status?, taskIds?, createdBy?, userId? }`.
`slug` defaults to the slugified title; `status` to `assigned` with an assignee,
else `draft`. `createdBy` is your session name (default `userId`).

```
POST /v1/handoffs
{ "project": "synatyx", "title": "REST routes", "body": "# Brief", "assignee": "synatyx-w2" }
→ 201 { "handoff": { …metadata… } }
```

### `PATCH /v1/handoffs/{id}`

`{ body?, title?, status?, assignee?, author?, userId? }`. A body that differs
from the current one creates a new version (`author` is recorded on it); an
identical body does not.

```
PATCH /v1/handoffs/7c1e…
{ "body": "# Brief v2", "author": "synatyx-lead" }
→ 200 { "handoff": { …metadata…, "version": 2 } }
```

### `POST /v1/handoffs/{id}/reports`

`{ agent, text, status?, userId? }`. `status` moves the handoff in the same step.

```
POST /v1/handoffs/7c1e…/reports
{ "agent": "synatyx-w2", "text": "routes done", "status": "done" }
→ 201 { "report": { …, "statusAfter": "done" }, "handoff": { …metadata…, "status": "done" } }
```

## `DELETE /v1/users/{user_id}`

Hard-deletes that user's items from every collection — project collections, the
shared L4 collection, and per-project code indexes. For erasure requests, where
"still there but flagged" is not an answer.

```json
{ "userId": "…", "collectionsPurged": ["ctx_a", "ctx_users"] }
```

If any collection could not be purged the response is `500` with
`INTERNAL_ERROR` and lists both sets: a partial erasure must never report
success to someone answering a data-subject request.

Scope: this covers the vector store.

## `GET /v1/health`

```json
{ "status": "ok", "vectorStore": "ok" }
```

`200` when the vector store answers, `503` otherwise. Deliberately not a bare
`200` — a process that is up while its vector store is unreachable is exactly
what a dependent service's readiness probe exists to catch. The backend check is
cached for a few seconds, so probing every second costs the same as probing
every five.
