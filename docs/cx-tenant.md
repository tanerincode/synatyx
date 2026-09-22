# Provisioning the CX tenant

Paste-ready configuration for the Customer Experience service, which is the
first consumer of this deployment that is not its owner.

Two settings do the work: a scoped key that confines CX to its own projects,
and a retention policy that expires its conversation memory. Everything here
assumes the scoped-keys and retention changes are on the branch you are
deploying — see [merge order](#merge-order) below.

## Prerequisite: an owner key

```bash
AUTH_ADMIN_KEY=<a long random string>
```

Scoped keys do nothing without it. When `AUTH_ADMIN_KEY` is empty the server is
unauthenticated altogether, every request is allowed, and a scope restricts
nothing — the configuration would look careful and mean nothing.

## The CX key

Generate a fresh secret. It must not be the admin key: the entire point is that
holding this one grants less.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

```bash
AUTH_SCOPED_KEYS='[
  {
    "name": "cx",
    "key": "<the generated secret>",
    "projects": ["cx-"],
    "tools": [
      "context_ingest",
      "context_retrieve",
      "context_store",
      "context_summarize",
      "context_deprecate_source"
    ],
    "routes": [
      "/mcp",
      "/v1/health",
      "/v1/ingest",
      "/v1/retrieve",
      "/v1/memory/store",
      "/v1/memory/retrieve",
      "/v1/memory/summarize",
      "/v1/sources/deprecate"
    ]
  }
]'
```

The CX service sends it as `X-Auth-Key`, from `CX_<ENV>_SYNATYX_KEY`.

The route list is written out rather than given as the prefix `/v1/`, which
would have been shorter and wrong: the prefix also covers `/v1/users/...`, and
listing routes explicitly means a route added later has to be granted rather
than inherited.

### What this key deliberately cannot do

- **Touch any project outside `cx-`.** Refused before the request reaches a
  handler, whether the project arrives as `project` or as `session_id`.
- **Erase a user.** `context_erase_user` and `/v1/users/...` are withheld.
  Erasure stays an owner action; if CX needs to serve erasure requests later
  that should be a decision, not something it already had.
- **Read resources or prompts over MCP.** Those serve the owner's own brief
  from the server's default user.
- **Call anything with no project named.** An unscoped call lands in the
  default collection, which belongs to whoever set the server up.

Each of these answers `403` with a `detail` saying which one applied. Refusals
log the key's `name`, never the key.

## Retention for CX memory

```bash
GC_RETENTION_POLICIES='[
  {"prefix": "cx-", "layers": {"L1": 90, "L2": 90, "L3": "keep"}}
]'
```

Conversation memory (L1 and L2, stored under end-user ids) expires ninety days
after it was created. Knowledge (L3, under `cx-service`) is marked `keep`: it
never ages out and goes only by explicit deprecation, which is what
`context_deprecate_source` does on a re-sync.

The commitment is **inaccessibility at ninety days**. Expiry deprecates the
item, so it leaves retrieval at ninety days; the hard delete follows after
`GC_GRACE_PERIOD_DAYS`, thirty by default, so it leaves the store at a hundred
and twenty. If that ever has to become a deletion-at-ninety promise, shorten
the window rather than the grace period — the grace period is shared with every
other tenant.

Unlike the ordinary TTL, this window does not stretch for importance or
pinning. See [memory-hygiene.md](memory-hygiene.md#retention-policies--a-ceiling-not-a-heuristic).

## Checking it worked

With the CX key, against its own project — expect `200`:

```bash
curl -s -X POST "$SYNATYX_URL/v1/retrieve" \
  -H "X-Auth-Key: $CX_KEY" -H 'Content-Type: application/json' \
  -d '{"project": "cx-lens-ai", "query": "cancel subscription"}'
```

The two that prove the scope is real — both expect `403`:

```bash
# someone else's project
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$SYNATYX_URL/v1/retrieve" \
  -H "X-Auth-Key: $CX_KEY" -H 'Content-Type: application/json' \
  -d '{"project": "taty-v2", "query": "anything"}'

# a capability the key does not have
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE \
  "$SYNATYX_URL/v1/users/someone" -H "X-Auth-Key: $CX_KEY"
```

If either returns `200`, the key is not scoped — check that `AUTH_ADMIN_KEY` is
set and that `AUTH_SCOPED_KEYS` parsed (a malformed entry is logged at startup
and dropped, which leaves the key unrecognised and every request `401`).

## Merge order

Scoped keys first, then retention. Both touch the imports at the top of
`src/config.py`, so whichever merges second needs a one-line rebase. Nothing
structural.

Neither is required by the other, and neither changes behaviour for existing
callers: with `AUTH_SCOPED_KEYS` and `GC_RETENTION_POLICIES` unset, the server
behaves exactly as it did before.
