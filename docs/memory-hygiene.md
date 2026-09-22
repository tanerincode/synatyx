# Memory Hygiene — Staleness & Consolidation

A wrong memory is worse than no memory: the agent acts on retrieved context *confidently*. These two mechanisms keep the store truthful over time — one detects when the world changed underneath a memory, the other merges episodic noise into stable knowledge.

---

## Type-aware staleness

### File content hashes

When a stored fact refers to specific files, list them in `metadata.files`:

```
context_store(
  content="Auth middleware lives in src/auth.ts; JWT config in src/config.py",
  memory_layer="L3",
  metadata={"files": ["src/auth.ts", "src/config.py"], "fact_type": "file-location"},
  ...
)
```

At store time, Synatyx hashes each readable file (sha256, 16-hex prefix) into `metadata.file_hashes`. At retrieval time (`context_retrieve` and `context_brief`), the hashes are re-checked and any memory whose files changed or vanished comes back flagged:

```json
{
  "content": "Auth middleware lives in src/auth.ts...",
  "possibly_stale": true,
  "stale_files": ["src/auth.ts"]
}
```

**Agent rule:** treat a `possibly_stale` memory as a hypothesis, not a fact — verify against the file, then re-store the corrected fact and deprecate the old item.

Notes:
- Hashing happens where the server runs; in stdio mode (Claude Code, Cursor) that's the same machine as the repo, which is the intended setup. Unreadable paths are skipped at store time and never flagged.
- Flags are added only when something is stale — responses are unchanged for the common case.

### TTL decay per fact type

Different facts rot at different speeds. Tag items with `metadata.fact_type` and GC scales their effective TTL:

| `fact_type` | Multiplier | Rationale |
|---|---|---|
| `file-location` | ×0.3 | Paths and symbols move constantly |
| `config` | ×0.7 | Ports, env vars, flags change often |
| *(untagged)* | ×1.0 | Classic importance-only behaviour |
| `architecture` | ×1.5 | Design decisions hold for a long time |
| `preference` | ×3.0 | How the user works barely changes |

`effective_ttl = base_ttl × (1 + importance × GC_IMPORTANCE_MULTIPLIER) × type_multiplier`

Override the table with `GC_FACT_TYPE_MULTIPLIERS` (JSON) in `.env`. Unknown types are neutral (×1.0). Pinned items, checkpoints, L4, and skills remain immune to GC as before.

---

## Consolidation — episodic → semantic

Humans don't keep every episodic trace; sleep merges them into semantic knowledge. Ten session memories about Qdrant config should become one L3 fact. The `Consolidator` does exactly that:

1. **Scan** each project collection for non-deprecated L2 items (per user). Attempt records, pinned items, skills, and previous consolidations are never touched.
2. **Cluster** by embedding similarity (greedy, cosine ≥ `CONSOLIDATION_SIMILARITY_THRESHOLD`, default 0.83).
3. **Merge** every cluster of ≥ `CONSOLIDATION_MIN_CLUSTER_SIZE` (default 3) into one L3 item:
   - content: newest-first bullet list, prefixed `[Consolidated from N episodic memories]` (deterministic, no LLM dependency)
   - embedding: cluster centroid — exactly what the members collectively matched on
   - importance: max of the cluster (capped at 0.9 so consolidations never become GC-immune)
   - `metadata: {type: "consolidated", consolidated_from: [...ids]}`
4. **Deprecate** the originals (never deleted) and link each to the merged item with a `supersedes` edge — history stays navigable via `context_related` and visible in `context_visualize`.

### When it runs

- **Background:** after every GC pass in the GC daemon (`RUN_MODE=gc`), when `CONSOLIDATION_ENABLED=true`.
- **On demand:** the `context_consolidate` MCP tool runs one pass over the active project's collection — useful for stdio-only setups without the daemon.

`CONSOLIDATION_MAX_MERGES_PER_RUN` (default 20) caps each pass as a safety valve; a failed cluster merge is logged and skipped, never fatal.

## Retention policies — a ceiling, not a heuristic

Everything above is about forgetting what nobody uses. A retention policy is a
different promise: that a tenant's data will be *gone* by a certain date.

The distinction matters because the two pull in opposite directions. A TTL
measures idleness and stretches for important or pinned items, because keeping
a useful memory longer is a feature. A retention commitment measures age and
stretches for nothing — a promise that quietly exempts whatever someone marked
important is not a promise.

Configure per project prefix with `GC_RETENTION_POLICIES`:

```bash
GC_RETENTION_POLICIES='[
  {"prefix": "cx-", "layers": {"L1": 90, "L2": 90, "L3": "keep"}}
]'
```

A number is days from creation. `"keep"` means age never expires that layer —
it goes only by explicit deprecation. A layer the policy does not mention, and
a project no prefix matches, keep the ordinary TTL behaviour unchanged.

When a policy applies, it is checked first and overrides every exemption the
collector otherwise honours:

| | Ordinary TTL | Retention policy |
|---|---|---|
| Measured from | last access, else creation | creation, always |
| Importance scaling | yes | no |
| `fact_type` multipliers | yes | no |
| Pinned items | exempt | **not exempt** |
| `importance >= 1.0` | exempt | **not exempt** |

An item under a policy with no creation date is expired rather than kept: its
age cannot be shown to be inside the promise, and "we could not tell" is not an
answer to give someone asking about their data.

The longest matching prefix wins, so `cx-lens` overrides `cx-` for that tenant.

Expiry deprecates, as the TTL path does; the hard delete follows after
`GC_GRACE_PERIOD_DAYS`. A retention window of 90 days therefore means data
leaves retrieval at 90 days and the store at 90 + grace. Set the window with
that in mind if the commitment is to deletion rather than to inaccessibility.
