import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";
import { api, auth, MOCK_ALL, onUnauthorized } from "./api/client";
import { Palette, Sidebar, Topbar } from "./components/Shell";
import { projectsOf, useTheme, type ShellContext } from "./lib/nav";
import { Icon, SkeletonBlock, ToastProvider } from "./components/ui";
import { BASE_PATH } from "./lib/base";
import { scoped } from "./lib/scope";
import { RefreshContext, useApi } from "./lib/useApi";
import { OverviewPage } from "./pages/Overview";

// Every page but Overview loads on demand (one chunk each); each page owns its
// own file, so page PRs never touch this one.
const MemoriesPage = lazy(() => import("./pages/Memories"));
const BriefsPage = lazy(() => import("./pages/Briefs"));
const GraphPage = lazy(() => import("./pages/Graph"));
const TasksPage = lazy(() => import("./pages/Tasks"));
const SkillsPage = lazy(() => import("./pages/Skills"));
const CodeIndexPage = lazy(() => import("./pages/CodeIndex"));
const UsagePage = lazy(() => import("./pages/Usage"));
const HygienePage = lazy(() => import("./pages/Hygiene"));

function KeyScreen({ onKey, rejected }: { onKey: (k: string) => void; rejected: boolean }) {
  const [key, setKey] = useState("");
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    if (key.trim()) onKey(key.trim());
  };
  return (
    <div className="key-screen">
      <form className="card key-card" onSubmit={submit}>
        <div className="brand" style={{ justifyContent: "center", padding: "0 0 14px" }}>
          <span className="mark" />
          <span className="word">Synatyx</span>
        </div>
        <h1>Admin key</h1>
        <p className="muted small">The dashboard reads /dashboard/api with the server's admin key. It is kept in this tab's session storage only.</p>
        <label className="input-icon" style={{ display: "flex", margin: "16px 0 10px" }}>
          <Icon name="key" />
          <input className="input" style={{ width: "100%" }} type="password" autoFocus autoComplete="current-password" placeholder="Paste the admin key" aria-label="Admin key" value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        {rejected && <p className="small" role="alert" style={{ color: "var(--critical)", marginBottom: 10 }}>That key was rejected. Check SYNATYX admin key settings and try again.</p>}
        <button className="btn primary" style={{ width: "100%", justifyContent: "center" }} type="submit">Open dashboard</button>
      </form>
    </div>
  );
}

function Layout() {
  const [theme, toggleTheme] = useTheme();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("synatyx_sidebar") === "collapsed");
  const [palette, setPalette] = useState(false);
  const navigate = useNavigate();
  const [sp] = useSearchParams();
  const overview = useApi("shell:overview", () => api.overview());
  const briefs = useApi("shell:briefs", () => api.briefs());
  useEffect(() => {
    const h = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const collapse = (): void => {
    localStorage.setItem("synatyx_sidebar", collapsed ? "open" : "collapsed");
    setCollapsed(!collapsed);
  };
  const projects = projectsOf(overview.data);
  return (
    <div className={`app${collapsed ? " collapsed" : ""}`}>
      <Sidebar overview={overview.data} briefs={briefs.data?.briefs} collapsed={collapsed} onCollapse={collapse} />
      <div className="main">
        <Topbar overview={overview.data} theme={theme} onTheme={toggleTheme} onPalette={() => setPalette(true)} />
        <main className="page">
          <Suspense fallback={<SkeletonBlock lines={6} />}>
            <Outlet context={{ overview: overview.data, projects } satisfies ShellContext} />
          </Suspense>
        </main>
      </div>
      <Palette open={palette} onClose={() => setPalette(false)} onTheme={toggleTheme} onNewBrief={() => navigate(scoped("/briefs", sp, { new: "1" }))} projects={projects} />
    </div>
  );
}

export function App() {
  const [hasKey, setHasKey] = useState(() => MOCK_ALL || auth.get() !== "");
  const [rejected, setRejected] = useState(false);
  const [tick, setTick] = useState(0);
  const [syncedAt, setSyncedAt] = useState(() => Date.now());
  useEffect(
    () =>
      onUnauthorized(() => {
        setRejected(true);
        setHasKey(false);
      }),
    [],
  );
  const bump = useCallback(() => {
    setTick((t) => t + 1);
    setSyncedAt(Date.now());
  }, []);
  const refresh = useMemo(() => ({ tick, bump, syncedAt }), [tick, bump, syncedAt]);
  if (!hasKey)
    return (
      <KeyScreen
        rejected={rejected}
        onKey={(k) => {
          auth.set(k);
          setRejected(false);
          setHasKey(true);
        }}
      />
    );
  return (
    <RefreshContext.Provider value={refresh}>
      <ToastProvider>
        <BrowserRouter basename={BASE_PATH}>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<OverviewPage />} />
              <Route path="memories" element={<MemoriesPage />} />
              <Route path="graph" element={<GraphPage />} />
              {/* Briefs owns its nested routes: board/list at /briefs, detail at /briefs/:id */}
              <Route path="briefs/*" element={<BriefsPage />} />
              <Route path="tasks" element={<TasksPage />} />
              <Route path="skills" element={<SkillsPage />} />
              <Route path="code" element={<CodeIndexPage />} />
              <Route path="usage" element={<UsagePage />} />
              <Route path="hygiene" element={<HygienePage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </RefreshContext.Provider>
  );
}
