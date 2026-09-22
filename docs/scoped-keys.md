# Scoped API keys

One Synatyx deployment can serve more than one consumer. A key that unlocks the
whole server is fine while the only consumer is its owner; it stops being fine
the moment a second service holds one, because that service can then read every
project on the server — including the owner's own memory.

A scoped key is restricted to a set of project prefixes, a set of tools, and a
set of routes. It cannot see anything else.

## Configuring

`AUTH_SCOPED_KEYS` is a JSON array. `AUTH_ADMIN_KEY` must also be set: without
an owner key there is no authentication at all, and scoping would be theatre.

```bash
AUTH_ADMIN_KEY=<a long random string>
AUTH_SCOPED_KEYS='[
  {
    "name": "cx",
    "key": "<a different long random string>",
    "projects": ["cx-"],
    "tools": ["context_ingest", "context_retrieve", "context_store",
              "context_summarize", "context_deprecate_source"],
    "routes": ["/mcp", "/v1/ingest", "/v1/retrieve", "/v1/health"]
  }
]'
```

The key is sent exactly like the admin key — `X-Auth-Key`, or `Authorization:
Bearer`.

| Field | Meaning |
| --- | --- |
| `name` | Appears in refusal logs. Not a secret. |
| `key` | The secret itself. Generate it like any other. |
| `projects` | Project slug **prefixes** this key may address. |
| `tools` | Tools this key may call, by exact name. |
| `routes` | Paths this key may request. A trailing `/` makes it a prefix. |

## Every list is exhaustive

An empty list grants **nothing**, never everything. A key with no `tools` can
call no tools; a key with no `projects` is refused every call that names one.

This is deliberate and it is the whole design. The alternative — empty meaning
"unrestricted" — turns a typo, a missing field or a bad merge into a key with
the run of the server, and it fails silently, because a key that works is a key
nobody looks at again.

## What is checked, and where

Authorization happens in the auth middleware, before the request reaches any
handler.

Over MCP every call is a `POST /mcp`, so the path says nothing about what is
being asked for. The middleware therefore reads the JSON-RPC body and checks
the tool name and the project argument in it, then replays the body to the
application untouched. Batched calls are checked message by message — one
disallowed message refuses the batch.

`session_id` is checked alongside `project`. It doubles as the project slug
across the tool surface, so a check that read only `project` could be bypassed
by sending the same value under the other name.

Only the handshake methods — `initialize`, `tools/list`, `ping` and the
lifecycle notifications — are allowed without naming a tool. `resources/read`
and `prompts/get` are refused outright for scoped keys: they serve the owner's
own brief from the server's default user, which is exactly the memory a tenant
key must not reach.

Over REST the route is checked, and so is the tool behind it. That second check
matters more than it looks: `"routes": ["/v1/"]` covers `/v1/users/...` as
well, so without it a prefix would quietly hand over user erasure while the
tool list carefully withheld the same capability over MCP.

A path the server does not recognise is refused rather than allowed, so routes
added in future have to be granted deliberately instead of being inherited by
whoever already holds a key.

## What a refusal looks like

`401` when the key matches nothing. `403` with a `detail` naming what was
refused, when the key is valid but the request is outside its scope:

```json
{ "error": "forbidden", "detail": "key 'cx' may not address project taty-v2" }
```

Refusals are logged with the key's `name`, never the key itself.

## What is not scoped

The admin key is unrestricted, and an OAuth access token carries the owner's
reach — it is issued only to someone who proved they hold the owner secret.
Scoped keys are for services; people authenticate as the owner.

Rate limiting is not part of a scope. A key restricted to one tenant can still
make as many calls as it likes within that tenant.
