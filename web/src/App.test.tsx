import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";

const overview = {
  totals: { total: 12, active: 10, deprecated: 2, pinned: 1, by_layer: { L1: 1, L2: 3, L3: 5, L4: 1 }, collections: 3, open_tasks: 4 },
  collections: [
    { collection: "ctx_kai", slug: "kai", is_user_global: false, total: 4, active: 3, deprecated: 1, pinned: 0, by_layer: { L1: 1, L2: 1, L3: 1, L4: 0 } },
    { collection: "ctx_synatyx", slug: "synatyx", is_user_global: false, total: 7, active: 6, deprecated: 1, pinned: 1, by_layer: { L1: 0, L2: 2, L3: 4, L4: 0 } },
    { collection: "ctx_users", slug: "users", is_user_global: true, total: 1, active: 1, deprecated: 0, pinned: 0, by_layer: { L1: 0, L2: 0, L3: 0, L4: 1 } },
  ],
  server: { version: "0.14.0", commit: "f4e76a7", tools: 46 },
};
const fixtures: Record<string, unknown> = {
  "/overview": overview,
  "/briefs": { briefs: [{ id: "b1", slug: "x", title: "Fix the thing", project: "synatyx", assignee: "w2", status: "blocked", version: 1, user_id: "tom", updated_at: "2026-10-19T10:00:00Z", last_report: { text: "waiting on #22", agent: "w2", at: "2026-10-19T10:00:00Z" } }] },
  "/tasks": { tasks: [] },
  "/usage": { days: 30, totals: { calls: 1, input_tokens: 1, output_tokens: 1, embedding_tokens: 1000, embedding_cost_usd: 0.02 }, by_tool: [], by_project: [], by_day: [] },
  "/skills": { skills: [] },
  "/hygiene": { collection: "ctx_synatyx", generated_at: "", expiring_7d: 0, deprecated: 0, pending_hard_delete: 0, protected: 0, duplicates: [], stale: [] },
};

beforeEach(() => {
  window.history.pushState({}, "", "/dashboard/");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url, "http://x").pathname.replace("/dashboard/api", "");
    return new Response(JSON.stringify(fixtures[path] ?? {}), { status: path in fixtures ? 200 : 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("asks for the admin key first and keeps it in session storage", async () => {
    render(<App />);
    const input = screen.getByLabelText("Admin key");
    await userEvent.type(input, "secret");
    await userEvent.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(sessionStorage.getItem("synatyx_admin_key")).toBe("secret");
    expect(await screen.findByRole("heading", { name: "Overview", level: 1 })).toBeInTheDocument();
  });

  it("renders the shell and overview from the API", async () => {
    sessionStorage.setItem("synatyx_admin_key", "k");
    render(<App />);
    const nav = screen.getByRole("complementary", { name: "Main navigation" });
    for (const label of ["Overview", "Memories", "Graph", "Briefs", "Tasks", "Skills", "Code index", "Usage", "Hygiene"]) {
      expect(within(nav).getByRole("link", { name: new RegExp(label) })).toBeInTheDocument();
    }
    expect(await screen.findByTitle("server version · commit")).toHaveTextContent("v0.14.0 · f4e76a7");
    expect(await within(nav).findByText("1 blocked")).toBeInTheDocument();
    expect(await screen.findByText("Blocked brief · Fix the thing")).toBeInTheDocument();
  });

  it("puts the project in the URL and navigates with it", async () => {
    sessionStorage.setItem("synatyx_admin_key", "k");
    render(<App />);
    const sw = await screen.findByTestId("project-switcher");
    await userEvent.click(within(sw).getByRole("button"));
    await userEvent.click(await within(sw).findByRole("option", { name: /synatyx/ }));
    await waitFor(() => expect(window.location.search).toBe("?project=synatyx"));
    await userEvent.click(within(screen.getByRole("complementary", { name: "Main navigation" })).getByRole("link", { name: /Tasks/ }));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe("/dashboard/tasks?project=synatyx"));
    expect(await screen.findByRole("heading", { name: "Tasks", level: 1 })).toBeInTheDocument();
  });

  it("opens the command palette with ⌘K and jumps to a page", async () => {
    sessionStorage.setItem("synatyx_admin_key", "k");
    render(<App />);
    await screen.findByTitle("server version · commit");
    await userEvent.keyboard("{Meta>}k{/Meta}");
    const input = await screen.findByRole("textbox", { name: "Command" });
    await userEvent.type(input, "usage{Enter}");
    await waitFor(() => expect(window.location.pathname).toBe("/dashboard/usage"));
  });

  it("returns to the key screen when the server rejects the key", async () => {
    sessionStorage.setItem("synatyx_admin_key", "wrong");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 })));
    render(<App />);
    expect(await screen.findByText(/That key was rejected/)).toBeInTheDocument();
    expect(sessionStorage.getItem("synatyx_admin_key")).toBeNull();
  });
});
