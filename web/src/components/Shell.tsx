import { useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, collectionFor } from "../api/client";
import type { Brief, Overview } from "../api/types";
import { ago } from "../lib/format";
import { scoped, useScope } from "../lib/scope";
import { RefreshContext, useApi } from "../lib/useApi";
import { agentColor } from "../lib/labels";
import { NAV, projectColor, projectsOf } from "../lib/nav";
import { Icon, type IconName } from "./ui";

function Switcher({ label, value, color, items, onPick, onOpen, testId }: {
  label: string; value: string; color: string; items: { value: string; label: string; n?: number; color?: string }[];
  onPick: (v: string) => void; onOpen?: () => void; testId: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent): void => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);
  return (
    <div className="switcher" ref={ref} data-testid={testId}>
      <button
        className="sw-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen(!open);
        }}
      >
        <span className="k">{label}</span>
        <span className="dot" style={{ background: color }} />
        <b>{items.find((i) => i.value === value)?.label ?? value}</b>
        <span className="caret">▾</span>
      </button>
      <div className={`menu${open ? " open" : ""}`} role="listbox">
        {items.map((it, i) => (
          <div key={it.value || "all"}>
            {i === 1 && <div className="sep" />}
            <div
              role="option"
              aria-selected={it.value === value}
              className={`mi${it.value === value ? " sel" : ""}`}
              onClick={() => {
                onPick(it.value);
                setOpen(false);
              }}
            >
              <span className="dot" style={{ background: it.color ?? "var(--line-2)" }} />
              {it.label}
              {it.n !== undefined && <span className="n">{it.n}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Sidebar({ overview, briefs, collapsed, onCollapse }: { overview?: Overview; briefs?: Brief[]; collapsed: boolean; onCollapse: () => void }) {
  const loc = useLocation();
  const [sp] = useSearchParams();
  const { project } = useScope();
  const active = (p: string): boolean => (p === "/" ? loc.pathname === "/" : loc.pathname.startsWith(p));
  const col = overview?.collections.find((c) => c.slug === project);
  const memCount = project ? col?.active : overview?.totals.active;
  const scopedBriefs = (briefs ?? []).filter((b) => !project || b.project === project);
  const blocked = scopedBriefs.filter((b) => b.status === "blocked").length;
  const open = scopedBriefs.filter((b) => !["done", "cancelled"].includes(b.status)).length;
  const count = (path: string): ReactNode => {
    if (path === "/memories" && memCount !== undefined) return <b className="count">{memCount}</b>;
    if (path === "/briefs" && briefs) return blocked ? <b className="count alert">{blocked} blocked</b> : <b className="count">{open}</b>;
    if (path === "/tasks" && overview) return <b className="count">{overview.totals.open_tasks}</b>;
    return null;
  };
  return (
    <aside className="sidebar" aria-label="Main navigation">
      <Link className="brand" to={scoped("/", sp)}>
        <span className="mark" />
        <span>
          <div className="word">Synatyx</div>
          <div className="sub">memory core</div>
        </span>
      </Link>
      {(["Memory", "Work", "System"] as const).map((g) => (
        <nav key={g} aria-label={g}>
          <div className="nav-group">{g}</div>
          {NAV.filter((n) => n.group === g).map((n) => (
            <Link key={n.path} className={`nav-item${active(n.path) ? " active" : ""}`} to={scoped(n.path, sp)} title={n.label} aria-current={active(n.path) ? "page" : undefined}>
              <Icon name={n.icon} />
              <span>{n.label}</span>
              {count(n.path)}
            </Link>
          ))}
        </nav>
      ))}
      <div className="spacer" />
      <button className="nav-item collapse-btn" onClick={onCollapse} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
        <Icon name="chevron" />
        <span>Collapse</span>
      </button>
    </aside>
  );
}

export function Topbar({ overview, theme, onTheme, onPalette }: { overview?: Overview; theme: string; onTheme: () => void; onPalette: () => void }) {
  const { project, user, setScope } = useScope();
  const loc = useLocation();
  const { bump, syncedAt } = useContext(RefreshContext);
  const projects = projectsOf(overview);
  const [wantUsers, setWantUsers] = useState(false);
  // users across project collections; fetched only when the switcher opens
  const usersApi = useApi(wantUsers && overview ? `users:${projects.join(",")}` : null, async () => {
    const lists = await Promise.all([...projects, "users"].map((p) => api.users(collectionFor(p)).catch(() => ({ users: [] }))));
    const totals = new Map<string, number>();
    lists.flatMap((l) => l.users).forEach((u) => totals.set(u.user_id, (totals.get(u.user_id) ?? 0) + u.total));
    return [...totals.entries()].sort((a, b) => b[1] - a[1]);
  });
  const nav = NAV.find((n) => (n.path === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.path)));
  const [, force] = useState(0);
  useEffect(() => {
    const t = setInterval(() => force((n) => n + 1), 15000);
    return () => clearInterval(t);
  }, []);
  const userItems = [{ value: "", label: "All users" }, ...(usersApi.data ?? (user ? [[user, 0] as [string, number]] : [])).map(([u, n]) => ({ value: u, label: u, n: n || undefined, color: agentColor(u) }))];
  return (
    <header className="topbar">
      <div className="crumb">{nav?.label ?? "Synatyx"}</div>
      <div className="grow" />
      <Switcher
        testId="project-switcher"
        label="Project"
        value={project}
        color={project ? projectColor(project, projects) : "linear-gradient(135deg,var(--cyan),var(--violet))"}
        items={[
          { value: "", label: "All projects", n: overview?.totals.active, color: "linear-gradient(135deg,var(--cyan),var(--violet))" },
          ...projects.map((p) => ({ value: p, label: p, n: overview?.collections.find((c) => c.slug === p)?.active, color: projectColor(p, projects) })),
        ]}
        onPick={(v) => setScope({ project: v })}
      />
      <Switcher testId="user-switcher" label="User" value={user} color={user ? agentColor(user) : "var(--line-2)"} items={userItems} onOpen={() => setWantUsers(true)} onPick={(v) => setScope({ user: v })} />
      <button className="search-btn" onClick={onPalette} aria-label="Open command palette">
        <Icon name="search" />
        <span className="lbl">Search or jump to…</span>
        <kbd>⌘K</kbd>
      </button>
      <span className="synced" title="Last synced">
        <i />
        <span className="txt">synced {ago(new Date(syncedAt).toISOString())}</span>
      </span>
      <button className="icon-btn" onClick={bump} title="Refresh this page" aria-label="Refresh">
        <Icon name="refresh" />
      </button>
      <button className="icon-btn" onClick={onTheme} title="Toggle theme" aria-label="Toggle theme">
        <Icon name={theme === "dark" ? "sun" : "moon"} />
      </button>
      {overview && (
        <span className="ver" title="server version · commit">
          v{overview.server.version} · {overview.server.commit}
        </span>
      )}
    </header>
  );
}

// ── ⌘K palette ──────────────────────────────────────────────────
interface PalItem {
  group: string;
  icon?: IconName;
  layer?: string;
  text: string;
  sub?: string;
  run: () => void;
}
export function Palette({ open, onClose, onTheme, onNewBrief, projects }: { open: boolean; onClose: () => void; onTheme: () => void; onNewBrief: () => void; projects: string[] }) {
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const { project, setScope } = useScope();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 180);
    return () => clearTimeout(t);
  }, [q]);
  const briefs = useApi(open ? "pal:briefs" : null, () => api.briefs());
  const memCollection = collectionFor(project || projects[0] || "users");
  const mems = useApi(open && debounced.length > 1 ? `pal:mem:${memCollection}:${debounced}` : null, () => api.itemsPage({ collection: memCollection, q: debounced, limit: 6 }));
  const ql = q.toLowerCase();
  const items: PalItem[] = [
    ...NAV.filter((n) => n.label.toLowerCase().includes(ql)).map((n) => ({ group: "Pages", icon: n.icon, text: n.label, sub: "go to", run: () => navigate(scoped(n.path, sp)) })),
    ...(briefs.data?.briefs ?? [])
      .filter((b) => b.title.toLowerCase().includes(ql) || b.slug.includes(ql))
      .slice(0, 6)
      .map((b) => ({ group: "Briefs", icon: "briefs" as const, text: b.title, sub: `${b.status.replace("_", " ")} · ${b.project}`, run: () => navigate(scoped(`/briefs/${b.id}`, sp, { owner: b.user_id })) })),
    ...(debounced.length > 1 ? mems.data?.items ?? [] : []).map((m) => ({
      group: `Memories · ${project || projects[0] || ""}`, layer: m.memory_layer, text: m.content, sub: m.memory_layer,
      run: () => navigate(scoped("/memories", sp, { q: debounced, item: m.id, ...(project ? {} : { collection: memCollection }) })),
    })),
    ...[
      { group: "Actions", icon: "plus" as const, text: "New brief", run: onNewBrief },
      { group: "Actions", icon: "sun" as const, text: "Toggle theme", run: onTheme },
      ...projects.map((p) => ({ group: "Actions", icon: "folder" as const, text: `Switch project: ${p}`, run: () => setScope({ project: p }) })),
      { group: "Actions", icon: "folder" as const, text: "Switch project: All projects", run: () => setScope({ project: "" }) },
    ].filter((a) => a.text.toLowerCase().includes(ql)),
  ];
  const cur = Math.min(sel, Math.max(0, items.length - 1));
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.querySelector(".pal-i.sel")?.scrollIntoView({ block: "nearest" });
  }, [cur]);
  if (!open) return null;
  const run = (it: PalItem | undefined): void => {
    if (!it) return;
    onClose();
    it.run();
  };
  let lastGroup = "";
  return (
    <div className="palette open" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="pal" role="dialog" aria-label="Command palette">
        <div className="pal-in">
          <Icon name="search" />
          <input
            autoFocus
            value={q}
            placeholder="Jump to a page, search memories, open a brief…"
            aria-label="Command"
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSel(Math.min(items.length - 1, cur + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setSel(Math.max(0, cur - 1));
              } else if (e.key === "Enter") run(items[cur]);
              else if (e.key === "Escape") onClose();
            }}
          />
          <kbd>esc</kbd>
        </div>
        <div className="pal-list" ref={listRef}>
          {items.length === 0 && <div className="empty-state" style={{ padding: 28 }}><p>Nothing matches “{q}”.</p></div>}
          {items.map((it, i) => {
            const head = it.group !== lastGroup ? <div className="pal-g">{it.group}</div> : null;
            lastGroup = it.group;
            return (
              <div key={`${it.group}-${i}`}>
                {head}
                <div className={`pal-i${i === cur ? " sel" : ""}`} onMouseMove={() => i !== cur && setSel(i)} onClick={() => run(it)}>
                  {it.layer ? <span className="dotc" style={{ background: `var(--${it.layer.toLowerCase()})`, margin: "0 4px" }} /> : it.icon && <Icon name={it.icon} />}
                  <span className="txt">{it.text}</span>
                  {it.sub && <span className="sub">{it.sub}</span>}
                </div>
              </div>
            );
          })}
        </div>
        <div className="pal-f">
          <span><kbd>↑</kbd><kbd>↓</kbd>move</span>
          <span><kbd>↵</kbd>open</span>
          <span><kbd>⌘K</kbd>toggle</span>
        </div>
      </div>
    </div>
  );
}
