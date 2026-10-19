// Deterministic mock data: 3 projects (kai, coverdropai, synatyx), ~200
// memories with relations, 9 briefs with report threads, 15 tasks, 8 skills,
// 30 days of usage. Mutable, so mocked actions (pin, PATCH, POST) persist for
// the session. Used only through client.ts.
import type {
  BriefDetail,
  BriefStatus,
  GraphEdge,
  IndexChunk,
  IndexInfo,
  Layer,
  MemoryItem,
  Priority,
  RelationType,
  SkillDetail,
  Task,
  TaskStatus,
  Usage,
} from "./types";

export interface MockItem extends MemoryItem {
  truncated: boolean;
  checkpoint_name: string | null;
  session_id: string | null;
  confidence: number;
  fact_type: string | null;
  files: string[];
  possibly_stale: boolean;
}
export interface MockBrief extends BriefDetail {
  bodies: Record<number, string>;
}
export interface MockStore {
  now: number;
  projects: string[];
  users: string[];
  agents: string[];
  items: MockItem[];
  edges: GraphEdge[];
  briefs: MockBrief[];
  tasks: Task[];
  skills: SkillDetail[];
  indexes: IndexInfo[];
  chunks: Record<string, IndexChunk[]>;
  usage: Usage;
  duplicates: { a: string; b: string; similarity: number }[];
  server: { version: string; commit: string; tools: number };
}

const H = 3600e3;
const D = 24 * H;

export function buildMockStore(now = Date.parse("2026-10-19T21:40:00Z")): MockStore {
  let seed = 7;
  const rnd = (): number => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const pick = <T,>(a: readonly T[]): T => a[Math.floor(rnd() * a.length)] as T;
  const iso = (t: number): string => new Date(t).toISOString();
  const hex = (n: number): string =>
    Array.from({ length: n }, () => "0123456789abcdef"[Math.floor(rnd() * 16)]).join("");
  const uid = (): string => `${hex(8)}-${hex(4)}-${hex(4)}-${hex(4)}-${hex(12)}`;

  const projects = ["kai", "coverdropai", "synatyx"];
  const users = ["tombastaner", "kai-bot", "ci-runner"];
  const origins = ["user-stated", "agent-inferred", "observer", "ingested-from-file", "ingested-from-web"];

  const seedFacts: Record<string, Partial<Record<Layer, string[]>>> = {
    synatyx: {
      L3: [
        "Qdrant holds one collection per project, named ctx_<slug>; the code index lives beside it as ctx_<slug>__index.",
        "RUN_MODE=mcp starts the stdio transport; RUN_MODE=http serves MCP over streamable HTTP plus /dashboard and /v1.",
        "The dashboard is a Vite + React app built to web/dist and served by Starlette at /dashboard; its JSON lives under /dashboard/api/* behind the admin key.",
        "context_brief is token-budgeted (default 2000) and assembles identity, last session, knowledge, attempts and open tasks in one call.",
        "Embeddings use text-embedding-3-small (1536 dims); embedding cost is tracked per tool call in the usage table.",
        "Handoffs are versioned briefs: a body edit bumps the version, an identical body does not. Reports carry agent, text and status_after.",
        "GC decays memories by fact_type: file-location rots fastest, preference barely rots. Pinned checkpoints are protected.",
        "`make check` runs ruff, mypy and pytest, plus `make web-check` when web/ and node are present.",
        "Postgres stores skills, tasks, handoffs and usage; Qdrant stores vectors; Redis holds L1 and the active-project pointer.",
        "Alternative detection runs after every L2–L4 store: near-identical purpose auto-links with alternative_to.",
        "The OAuth 2.1 provider issues tokens for MCP clients; the admin key guards /dashboard/api and is never accepted on /v1.",
        "The Docker image builds the dashboard in a node:22-slim stage; the runtime stays python:3.12-slim with no Node.",
      ],
      L2: [
        "Session 2026-10-19: shipped context_handoff_* tools (create/get/list/update/report) with task assignee; 34 tests green.",
        "Tried auto-orbiting 3D graph for the dashboard memory view — unreadable past 150 nodes and burns CPU. Going 2D force graph instead.",
        "Session 2026-10-17: added /v1 JSON API for backend callers; reused the MCP dispatcher so tool and REST behaviour cannot drift.",
        "Tried storing L1 in Qdrant to unify retrieval — failed: write amplification on every message. Kept Redis for L1.",
        "Session 2026-10-15: index push/remove tools landed; staleness check hashes file contents, not mtimes.",
      ],
      L1: [
        "user: why is the overview count different from context_list?",
        "assistant: overview counts deprecated items in total; context_list hides them unless include_deprecated.",
        "user: let's make the brief kanban the first thing after overview",
      ],
    },
    kai: {
      L3: [
        "Kai routes builder work to per-project worktrees under .claude/worktrees/<project>-wN; leads never merge.",
        'Sir approves design before build: nothing moves to step 2 until he says "build".',
        "Kai Bridge stores briefs at kai/projects/<project>/briefs/*.md and design notes under design/.",
        "Telegram is the out-of-band channel for Sir; long tasks end with a fresh reply so the phone pings.",
        "Phase 1 handoffs work has priority on the machine; heavy runs are kept short.",
      ],
      L2: [
        "Session 2026-10-19: v0 credits ran out; dashboard redesign moved in-house to synatyx-w4.",
        "Tried pkill to clean stray dev servers — killed another session's process. Rule now: stop own pids only, leaves first.",
        "Session 2026-10-18: roster split into lead / builder / reviewer / shipper; reviewer is read-only.",
      ],
      L1: ["user: show me the shots when they're ready", "assistant: forwarding to synatyx-lead"],
    },
    coverdropai: {
      L3: [
        "CoverDrop renders covers with a Flux pipeline behind a queue; jobs time out after 90 s and retry once.",
        "Stripe webhooks drive credit top-ups; the webhook secret rotates monthly.",
        "The landing page ships from Vercel; the API is a FastAPI service on Fly.io in fra.",
        "Image storage is R2 with signed URLs that expire after 24 h.",
        "Prompt templates are versioned in prompts/; the active version is pinned in settings.PROMPT_VERSION.",
      ],
      L2: [
        "Session 2026-10-16: fixed double credit charge — webhook retried before idempotency key was stored.",
        "Tried SDXL turbo for previews — quality too low for faces. Kept Flux schnell for previews.",
        "Session 2026-10-12: moved queue from RQ to Arq for asyncio; throughput up 2.4×.",
      ],
      L1: ["user: can we cap previews at 4 per minute?"],
    },
  };
  const L4 = [
    "No AI attribution in git, anywhere: no Co-Authored-By, no Generated-with lines, no session links.",
    "Prefers conventional commits and small PRs reviewed by another agent before merge.",
    "Never kill processes by name or pattern; stop only your own pids, leaves first.",
    "Dark theme first, light theme second, for every UI.",
    "Reports lead with the outcome in one sentence, then verification output.",
    "Tom's working hours are Istanbul time (UTC+3).",
  ];
  const filler: Record<"L1" | "L2" | "L3", string[]> = {
    L3: [
      "Config: %s reads its limits from settings.%s; defaults live in config/defaults.yaml.",
      "The %s module owns %s; callers go through the service, never the repository directly.",
      "Convention: %s endpoints return {error} with a 4xx instead of raising; %s logs at warning.",
    ],
    L2: [
      "Session summary: worked on %s; %s is done, follow-up left open.",
      "Attempt: %s via %s — failed, reverted. Notes in the PR thread.",
      "Session summary: reviewed %s; asked for a test on %s before merge.",
    ],
    L1: ["user: what does %s do with %s?", "assistant: %s is called from %s on every request."],
  };
  const words = [
    "retrieval", "scorer", "gc daemon", "index push", "usage meter", "oauth flow", "handoff service",
    "task board", "skill store", "rest router", "ingest pipeline", "consolidation", "embedding cache", "webhook", "queue",
  ];

  const items: MockItem[] = [];
  const mk = (project: string, layer: Layer, content: string, extra: Partial<MockItem> = {}): MockItem => {
    const created = now - Math.floor(rnd() * (layer === "L1" ? 9 * D : 60 * D));
    const it: MockItem = {
      id: uid(),
      content,
      truncated: false,
      memory_layer: layer,
      importance: +(layer === "L4" ? 0.75 + rnd() * 0.25 : layer === "L3" ? 0.45 + rnd() * 0.5 : 0.2 + rnd() * 0.5).toFixed(2),
      confidence: +(0.6 + rnd() * 0.4).toFixed(2),
      is_pinned: false,
      is_deprecated: false,
      possibly_stale: false,
      origin: layer === "L4" ? "user-stated" : pick(layer === "L3" ? origins : ["agent-inferred", "observer", "user-stated"]),
      type: null,
      checkpoint_name: null,
      user_id: layer === "L4" ? "tombastaner" : pick([...users, "tombastaner", "tombastaner"]),
      session_id: project,
      project,
      created_at: iso(created),
      fact_type: layer === "L3" ? pick(["architecture", "config", "file-location", "architecture"]) : layer === "L4" ? "preference" : null,
      files: [],
      ...extra,
    };
    items.push(it);
    return it;
  };

  for (const p of projects) {
    for (const L of ["L3", "L2", "L1"] as const) {
      for (const c of seedFacts[p]?.[L] ?? []) {
        const it = mk(p, L, c);
        if (/^Tried|^Attempt/.test(c)) it.type = "attempt";
      }
    }
  }
  L4.forEach((c) => mk("users", "L4", c));
  let i = 0;
  while (items.length < 196) {
    const p = projects[i++ % 3] as string;
    const L: "L1" | "L2" | "L3" = rnd() < 0.5 ? "L3" : rnd() < 0.7 ? "L2" : "L1";
    const c = pick(filler[L]).replace("%s", pick(words)).replace("%s", pick(words));
    const it = mk(p, L, c);
    if (/^Attempt/.test(c)) it.type = "attempt";
  }
  const synL3 = items.filter((x) => x.project === "synatyx" && x.memory_layer === "L3");
  const s = (k: number): MockItem => synL3[k] as MockItem;
  Object.assign(s(0), { is_pinned: true, checkpoint_name: "collection-per-project", files: ["src/storage/qdrant.py", "src/core/project.py"] });
  Object.assign(s(5), { is_pinned: true, checkpoint_name: "handoff-model-v1" });
  Object.assign(s(2), { files: ["src/transports/mcp/dashboard/page.py", "web/src/main.tsx"], possibly_stale: true });
  Object.assign(s(7), { files: ["Makefile"] });
  Object.assign(s(11), { files: ["Dockerfile"] });
  items
    .filter((x) => x.memory_layer === "L3")
    .forEach((x, k) => {
      if (k % 9 === 3) x.is_deprecated = true;
      if (k % 11 === 5 && !x.files.length) {
        x.possibly_stale = true;
        x.files = [`src/${pick(["core", "storage", "services"])}/${pick(words).replace(/ /g, "_")}.py`];
      }
      if (k % 13 === 1 && !x.is_deprecated) {
        x.is_pinned = true;
        x.checkpoint_name = x.checkpoint_name ?? `${pick(words).replace(/ /g, "-")}-decision`;
      }
    });
  const firstL4 = items.find((x) => x.memory_layer === "L4");
  if (firstL4) firstL4.is_pinned = true;

  const edges: GraphEdge[] = [];
  const relWeighted: RelationType[] = [
    "related_to", "related_to", "part_of", "depends_on", "supersedes", "caused_by", "used_for", "related_to",
  ];
  for (const p of projects) {
    const pool = items.filter((x) => x.project === p && x.memory_layer !== "L1");
    for (let k = 0; k < pool.length * 1.2; k++) {
      const a = pick(pool);
      const b = pick(pool);
      if (a !== b) edges.push({ source: a.id, target: b.id, type: pick(relWeighted) });
    }
  }
  const old1 = mk("synatyx", "L3", "The dashboard is one HTML file with a 3D auto-orbiting memory cloud on the overview.", { is_deprecated: true, created_at: iso(now - 40 * D) });
  const old2 = mk("synatyx", "L3", "The dashboard has five tabs (Overview, Recent memories, Code indexes, Usage, Visualize) in one static HTML file.", { is_deprecated: true, created_at: iso(now - 12 * D) });
  edges.push(
    { source: old2.id, target: old1.id, type: "supersedes" },
    { source: s(2).id, target: old2.id, type: "supersedes" },
    { source: s(5).id, target: s(8).id, type: "depends_on" },
  );

  const dupDefs: [string, Layer, string, string, number][] = [
    ["synatyx", "L3", "Each project gets its own Qdrant collection ctx_<slug>; L4 always goes to ctx_users.", "Per-project Qdrant collections are named ctx_<slug>; user preferences (L4) live in the shared ctx_users collection.", 0.95],
    ["users", "L4", "Never use pkill or killall; stop only processes you started, by pid.", "Do not kill processes by name or pattern — other sessions share the Mac; kill own pids, leaves first.", 0.91],
    ["coverdropai", "L3", "Stripe webhook secret rotates monthly; update STRIPE_WEBHOOK_SECRET on Fly.", "The Stripe webhook signing secret is rotated every month (Fly secret STRIPE_WEBHOOK_SECRET).", 0.93],
    ["synatyx", "L2", "Tried 3D orbit graph for memories — unreadable past 150 nodes.", "Attempt: auto-orbiting 3D memory cloud failed; too many nodes to read, high CPU.", 0.88],
  ];
  const duplicates = dupDefs.map(([p, L, a, b, similarity]) => {
    const A = mk(p, L, a);
    const B = mk(p, L, b);
    edges.push({ source: B.id, target: A.id, type: "alternative_to" });
    return { a: A.id, b: B.id, similarity };
  });

  // ── briefs ──
  const agents = ["kai", "synatyx-lead", "synatyx-w1", "synatyx-w2", "synatyx-w3", "synatyx-w4", "reviewer", "shipper"];
  const rep = (hAgo: number, agent: string, text: string, status: BriefStatus | null) => ({ at: iso(now - hAgo * H), agent, text, status });
  const brief = (b: Omit<MockBrief, "id" | "user_id" | "bodies" | "created_at"> & { prevBody?: string }): MockBrief => {
    const { prevBody, ...rest } = b;
    const bodies: Record<number, string> = { [b.version]: b.body };
    if (prevBody) bodies[b.version - 1] = prevBody;
    const oldest = b.versions[b.versions.length - 1];
    return { ...rest, id: uid(), user_id: "tombastaner", bodies, created_at: oldest?.at ?? null };
  };
  const ver = (version: number, hAgo: number, author: string) => ({ version, at: iso(now - hAgo * H), author });
  const briefs: MockBrief[] = [
    brief({
      slug: "dashboard-build", title: "Dashboard redesign — build the Vite app", project: "synatyx", assignee: "synatyx-w4",
      status: "in_progress", version: 2, updated_at: iso(now - 0.3 * H),
      body: "# Dashboard build\n\nSir said **build** with Vite + React + TS.\n\n## Deliver\n\n- `web/` with strict TS, eslint, vitest and one typed API client.\n- Real endpoints first; new ones behind `USE_MOCKS`.\n- Dockerfile node stage; `make web-check`.\n\n## Rules\n\n- Do not touch `src/` until the serving go.\n- Never `pkill`; stop only your own pids.",
      prevBody: "# Dashboard build\n\nSir said **build**.\n\n## Deliver\n\n- `web/` with strict TS.\n\n## Rules\n\n- Never `pkill`.",
      reports: [
        rep(0.3, "synatyx-w4", "Shell, Overview and Memories done against real endpoints; Briefs next.", null),
        rep(2, "synatyx-w4", "Picked up; scaffolding web/.", "in_progress"),
        rep(2.5, "synatyx-lead", "Handed to w4 with the prototype as the source of truth.", "assigned"),
        rep(3, "kai", "Sir approved the prototype and picked Vite.", "draft"),
      ],
      versions: [ver(2, 2.5, "synatyx-lead"), ver(1, 3, "kai")],
    }),
    brief({
      slug: "items-paged", title: "Paged /items with q, origin, sort", project: "synatyx", assignee: "synatyx-w2",
      status: "blocked", version: 1, updated_at: iso(now - 3 * H),
      body: "# Paged items\n\nAdd `q`, `origin`, `type`, `stale`, `from`, `to`, `offset`, `sort` to `/dashboard/api/items` and return `total`.",
      reports: [
        rep(3, "synatyx-w2", "Blocked: scan_all_items has no offset for filtered scans; need a storage change first.", "blocked"),
        rep(7, "synatyx-w2", "Started.", "in_progress"),
        rep(9, "synatyx-lead", "Assigned.", "assigned"),
      ],
      versions: [ver(1, 9, "synatyx-lead")],
    }),
    brief({
      slug: "dashboard-package", title: "Split dashboard.py into a package", project: "synatyx", assignee: "synatyx-w3",
      status: "done", version: 1, updated_at: iso(now - 1.5 * H),
      body: "# Package\n\nMove the dashboard API into `src/transports/mcp/dashboard/` with `DASHBOARD_ROUTES`.",
      reports: [rep(1.5, "synatyx-w3", "Merged as #47.", "done"), rep(5, "synatyx-w3", "Started.", "in_progress"), rep(6, "synatyx-lead", "Assigned.", "assigned")],
      versions: [ver(1, 6, "synatyx-lead")],
    }),
    brief({
      slug: "hygiene-endpoint", title: "Hygiene endpoint (/dashboard/api/hygiene)", project: "synatyx", assignee: "synatyx-w1",
      status: "in_progress", version: 1, updated_at: iso(now - 2 * H),
      body: "# Hygiene\n\nGC stats, duplicate suggestions and stale items in one call.",
      reports: [rep(2, "synatyx-w1", "GC stats wired; duplicates next.", null), rep(4, "synatyx-w1", "Picked up.", "in_progress"), rep(5, "synatyx-lead", "Assigned.", "assigned")],
      versions: [ver(1, 5, "synatyx-lead")],
    }),
    brief({
      slug: "stack-review", title: "Review the Vite dashboard PR", project: "synatyx", assignee: null,
      status: "draft", version: 1, updated_at: iso(now - 1 * H),
      body: "# Review\n\nRead-only review of the dashboard PR: correctness, missed requirements, bundle size.",
      reports: [], versions: [ver(1, 1, "synatyx-lead")],
    }),
    brief({
      slug: "webhook-idempotency", title: "Stripe webhook idempotency audit", project: "coverdropai", assignee: "kai",
      status: "assigned", version: 2, updated_at: iso(now - 30 * H),
      body: "# Webhook idempotency\n\nAudit every Stripe handler for an idempotency key written **before** side effects.",
      prevBody: "# Webhook idempotency\n\nAudit every Stripe handler.",
      reports: [rep(30, "kai", "Self-assigned; scheduled for tomorrow.", "assigned"), rep(52, "kai", "Drafted after the double-charge incident.", "draft")],
      versions: [ver(2, 30, "kai"), ver(1, 52, "kai")],
    }),
    brief({
      slug: "preview-rate-limit", title: "Cap previews at 4 per minute per user", project: "coverdropai", assignee: "synatyx-w3",
      status: "done", version: 1, updated_at: iso(now - 50 * H),
      body: "# Preview rate limit\n\nToken bucket in Redis, 4/min per user, 429 with Retry-After.",
      reports: [rep(50, "synatyx-w3", "Shipped behind PREVIEW_RATE_LIMIT; tests cover burst and refill.", "done"), rep(66, "synatyx-w3", "Started.", "in_progress"), rep(70, "kai", "Assigned.", "assigned")],
      versions: [ver(1, 70, "kai")],
    }),
    brief({
      slug: "roster-doc", title: "Write the roster doc for Kai Bridge", project: "kai", assignee: "synatyx-lead",
      status: "assigned", version: 1, updated_at: iso(now - 14 * H),
      body: "# Roster\n\nOne page per role: lead, builder, reviewer, tester, shipper, ops, scout, researcher.",
      reports: [rep(14, "kai", "Assigned.", "assigned")], versions: [ver(1, 14, "kai")],
    }),
    brief({
      slug: "old-3d-graph", title: "Polish the 3D orbit graph", project: "synatyx", assignee: "synatyx-w3",
      status: "cancelled", version: 1, updated_at: iso(now - 4 * D),
      body: "# 3D graph polish\n\nSuperseded by the 2D force graph in the redesign.",
      reports: [rep(96, "kai", "Cancelled: redesign drops the 3D cloud.", "cancelled"), rep(140, "kai", "Assigned.", "assigned")],
      versions: [ver(1, 140, "kai")],
    }),
  ];

  const taskDefs: [string, TaskStatus, Priority, string, string | null][] = [
    ["Build the Vite dashboard", "in_progress", "high", "synatyx", "synatyx-w4"],
    ["Paged /items with q, origin, sort", "in_progress", "high", "synatyx", "synatyx-w2"],
    ["Hygiene endpoint", "in_progress", "medium", "synatyx", "synatyx-w1"],
    ["Item detail + pin/deprecate endpoints", "pending", "high", "synatyx", null],
    ["Skills endpoints", "pending", "medium", "synatyx", null],
    ["PATCH /dashboard/api/tasks/{id}", "pending", "medium", "synatyx", null],
    ["Remove the old dashboard.html", "pending", "low", "synatyx", null],
    ["Rotate Stripe webhook secret", "pending", "high", "coverdropai", "kai"],
    ["Fix double-charge on retried webhook", "done", "high", "coverdropai", "synatyx-w3"],
    ["Preview rate limit", "done", "medium", "coverdropai", "synatyx-w3"],
    ["Move landing analytics to Plausible", "cancelled", "low", "coverdropai", null],
    ["Roster doc", "pending", "medium", "kai", "synatyx-lead"],
    ["Telegram long-task ping", "done", "low", "kai", null],
    ["Nightly backup check", "pending", "medium", "kai", "ops"],
    ["Consolidate October L2 summaries", "pending", "low", "synatyx", null],
  ];
  const tasks: Task[] = taskDefs.map(([title, status, priority, project, assignee], k) => ({
    id: uid(), title, description: "", status, priority, project, assignee, user_id: "tombastaner", created_at: iso(now - (k + 1) * 7 * H), updated_at: iso(now - (k + 1) * 3 * H),
  }));

  const skillDefs: [string, string, string, string | null][] = [
    ["Conventional commit", "conventional-commit", "Write a conventional commit message from a staged diff, no attribution lines.", null],
    ["Release notes", "release-notes", "Turn merged PR titles since the last tag into grouped release notes.", "synatyx"],
    ["Qdrant collection audit", "qdrant-audit", "List collections, counts by layer, and orphaned __index collections.", "synatyx"],
    ["Playwright screenshots", "playwright-shots", "Capture full-page screenshots of a static site at fixed widths.", null],
    ["Stripe webhook test", "stripe-webhook-test", "Replay a Stripe event against the local API with a signed payload.", "coverdropai"],
    ["PR review checklist", "pr-review", "Read-only review: correctness, missed requirements, constraint violations.", null],
    ["Brief template", "brief-template", "Skeleton for a handoff brief: goal, deliverables, rules, definition of done.", "kai"],
    ["Flux prompt tuning", "flux-prompt-tuning", "Iterate a cover prompt against the eval set and record the winner.", "coverdropai"],
  ];
  const skills: SkillDetail[] = skillDefs.map(([name, slug, description, project], k) => ({
    name, slug, description, project, user_id: "tombastaner", updated_at: iso(now - (k * 3 + 1) * D), frontmatter: { name, description },
    content: `# ${name}\n\n${description}\n\n## When to use\n\n- When the task matches: *${description.toLowerCase()}*\n- Not for one-off scripts.\n\n## Steps\n\n1. Read the inputs.\n2. Do the work in small steps.\n3. Verify and report.\n\n\`\`\`bash\nmake check\n\`\`\``,
  }));

  const indexes: IndexInfo[] = [
    { collection: "ctx_synatyx__index", project: "synatyx", files: 214, chunks: 3870, by_language: { python: 160, markdown: 31, typescript: 14, yaml: 9 }, last_indexed_at: iso(now - 2 * H), users: ["tombastaner"] },
    { collection: "ctx_coverdropai__index", project: "coverdropai", files: 168, chunks: 2240, by_language: { python: 98, typescript: 61, markdown: 9 }, last_indexed_at: iso(now - 3 * D), users: ["tombastaner"] },
    { collection: "ctx_kai__index", project: "kai", files: 42, chunks: 390, by_language: { markdown: 35, json: 7 }, last_indexed_at: iso(now - 9 * D), users: ["tombastaner", "kai-bot"] },
  ];
  const files = [
    "src/core/retrieval.py", "src/core/scorer.py", "src/core/handoff.py", "src/services/memory_service.py",
    "src/storage/qdrant.py", "src/storage/postgres.py", "src/transports/mcp/server.py", "src/transports/mcp/http_server.py",
    "src/transports/mcp/dashboard/__init__.py", "src/transports/mcp/dashboard/page.py", "tests/test_handoff_tools.py",
    "docs/tools.md", "web/src/api/client.ts", "web/src/main.tsx",
  ];
  const chunks: Record<string, IndexChunk[]> = {};
  for (const ix of indexes) {
    chunks[ix.collection] = files.flatMap((path) => {
      const total = 1 + Math.floor(rnd() * 4);
      const lang = path.endsWith(".py") ? "python" : path.endsWith(".md") ? "markdown" : "typescript";
      return Array.from({ length: total }, (_, ci) => {
        const sym = pick(["api_overview", "list_items", "HandoffService.update", "score", "scan_all_items", "render", "request"]);
        const start = 1 + ci * 40;
        return {
          path, chunk_index: ci, chunk_total: total, symbol: sym, kind: lang === "markdown" ? "section" : "function", language: lang,
          line_start: start, line_end: start + 32,
          preview: lang === "python"
            ? `async def ${sym.split(".").pop()}(self, request: Request) -> JSONResponse:\n    storages = _storages(request)\n    if storages is None:\n        return JSONResponse({"error": "server not ready"}, status_code=503)`
            : lang === "markdown" ? `## ${sym}\n\nHow ${sym} behaves and when to call it.` : `export async function ${sym.split(".").pop()}(): Promise<void> {\n  const res = await request("/overview");\n  return res;\n}`,
          indexed_at: ix.last_indexed_at,
        };
      });
    });
  }

  const tools = ["context_retrieve", "context_brief", "context_pack", "context_store", "context_index_search", "context_handoff_get", "context_task_list", "context_skill_find"];
  const by_day: Usage["by_day"] = [];
  for (let d = 29; d >= 0; d--) {
    const t = now - d * D;
    const wk = new Date(t).getUTCDay() % 6 === 0 ? 0.55 : 1;
    const ramp = 0.7 + (30 - d) / 60;
    by_day.push({
      day: iso(t).slice(0, 10),
      output_tokens: Math.round((90000 + rnd() * 70000) * wk * ramp),
      input_tokens: Math.round((14000 + rnd() * 9000) * wk * ramp),
      embedding_tokens: Math.round((40000 + rnd() * 30000) * wk * ramp),
      calls: Math.round((300 + rnd() * 240) * wk * ramp),
    });
  }
  const sum = (k: "calls" | "input_tokens" | "output_tokens" | "embedding_tokens"): number => by_day.reduce((a, d) => a + d[k], 0);
  const shares = [0.31, 0.22, 0.17, 0.04, 0.12, 0.07, 0.04, 0.03];
  const usage: Usage = {
    days: 30,
    totals: { calls: sum("calls"), output_tokens: sum("output_tokens"), input_tokens: sum("input_tokens"), embedding_tokens: sum("embedding_tokens"), embedding_cost_usd: +((sum("embedding_tokens") * 0.02) / 1e6).toFixed(4) },
    by_tool: tools.map((tool, k) => ({ tool, calls: Math.round(1600 / (k + 1.3)), output_tokens: Math.round(sum("output_tokens") * (shares[k] ?? 0)), input_tokens: Math.round(sum("input_tokens") / 8), embedding_tokens: Math.round(sum("embedding_tokens") / 8) })),
    by_project: [
      ["synatyx", 0.58, 0.6, 0.62],
      ["kai", 0.27, 0.24, 0.2],
      ["coverdropai", 0.15, 0.16, 0.18],
    ].map(([project, c, o, e]) => ({ project: project as string, calls: Math.round(sum("calls") * (c as number)), output_tokens: Math.round(sum("output_tokens") * (o as number)), input_tokens: Math.round(sum("input_tokens") * (c as number)), embedding_tokens: Math.round(sum("embedding_tokens") * (e as number)) })),
    by_day,
  };

  return {
    now, projects, users, agents, items, edges, briefs, tasks, skills, indexes, chunks, usage, duplicates,
    server: { version: "0.14.0", commit: "6a3b53e", tools: 46 },
  };
}
