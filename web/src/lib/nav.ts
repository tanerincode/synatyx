import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import type { Overview } from "../api/types";
import type { IconName } from "../components/ui";

export const NAV: { path: string; label: string; icon: IconName; group: "Memory" | "Work" | "System" }[] = [
  { path: "/", label: "Overview", icon: "overview", group: "Memory" },
  { path: "/memories", label: "Memories", icon: "memories", group: "Memory" },
  { path: "/graph", label: "Graph", icon: "graph", group: "Memory" },
  { path: "/briefs", label: "Briefs", icon: "briefs", group: "Memory" },
  { path: "/tasks", label: "Tasks", icon: "tasks", group: "Work" },
  { path: "/skills", label: "Skills", icon: "skills", group: "Work" },
  { path: "/code", label: "Code index", icon: "code", group: "System" },
  { path: "/usage", label: "Usage", icon: "usage", group: "System" },
  { path: "/hygiene", label: "Hygiene", icon: "hygiene", group: "System" },
];
const PROJ_COL = ["#67e8f9", "#a78bfa", "#fb7185", "#34d399", "#fbbf24", "#60a5fa"];
export const projectColor = (p: string, all: string[]): string => PROJ_COL[Math.max(0, all.indexOf(p)) % PROJ_COL.length] as string;

export function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState(() => localStorage.getItem("synatyx_theme") ?? "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("synatyx_theme", theme);
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

/** Projects come from the overview's memory collections (ctx_users is L4, not a project). */
export function projectsOf(o: Overview | undefined): string[] {
  return (o?.collections ?? []).filter((c) => !c.is_user_global).map((c) => c.slug);
}


/** What the shell hands every page: the overview (projects, server) it already loaded. */
export interface ShellContext {
  overview: Overview | undefined;
  projects: string[];
}
export const useShell = (): ShellContext => useOutletContext<ShellContext>();
