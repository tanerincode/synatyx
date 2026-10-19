import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import type { Brief, BriefDetail } from "../../api/types";
import { ToastProvider } from "../../components/ui";
import BriefsPage from "../Briefs";

const list: Brief[] = [
  { id: "b1", slug: "build", title: "Build the board", project: "synatyx", assignee: "w4", status: "in_progress", version: 2, user_id: "tom", updated_at: "2026-10-19T10:00:00Z", last_report: { agent: "w4", text: "Board done", at: "2026-10-19T10:00:00Z" } },
  { id: "b2", slug: "rest", title: "REST routes", project: "synatyx", assignee: "w2", status: "blocked", version: 1, user_id: "tom", updated_at: "2026-10-19T09:00:00Z", last_report: { agent: "w2", text: "Waiting on #22", at: "2026-10-19T09:00:00Z" } },
  { id: "b3", slug: "old", title: "Old idea", project: "kai", assignee: null, status: "cancelled", version: 1, user_id: "ci", updated_at: "2026-10-10T09:00:00Z", last_report: null },
];
const detail: BriefDetail = {
  ...list[0]!, body: "# Build\n\nShip the **board**.", created_at: "2026-10-18T10:00:00Z",
  reports: [
    { at: "2026-10-19T10:00:00Z", agent: "w4", status: null, text: "Board done" },
    { at: "2026-10-19T08:00:00Z", agent: "w4", status: "in_progress", text: "Picked up" },
    { at: "2026-10-19T07:00:00Z", agent: "lead", status: "assigned", text: "Assigned to w4" },
  ],
  versions: [{ version: 2, at: "2026-10-19T07:00:00Z", author: "lead" }, { version: 1, at: "2026-10-18T10:00:00Z", author: "kai" }],
};

let calls: { url: string; method: string; body: unknown }[] = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const u = new URL(url, "http://x");
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url: u.pathname + u.search, method: init?.method ?? "GET", body });
    if (u.pathname === "/dashboard/api/briefs" && init?.method === "POST") return Response.json({ ...detail, id: "new1", title: body.title, user_id: body.user, version: 1, status: body.assignee ? "assigned" : "draft" }, { status: 201 });
    if (u.pathname === "/dashboard/api/briefs") return Response.json({ briefs: list });
    if (u.pathname === "/dashboard/api/briefs/b1" && u.searchParams.get("version") === "1") return Response.json({ ...detail, version: 1, body: "# Build\n\nShip it." });
    if (u.pathname === "/dashboard/api/briefs/b1") return Response.json(init?.method === "PATCH" ? { ...detail, ...body } : detail);
    return Response.json({ error: "brief not found" }, { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const l = useLocation();
  return <output data-testid="where">{l.pathname + l.search}</output>;
}
function renderAt(path: string) {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<><Outlet context={{ overview: undefined, projects: ["kai", "synatyx"] }} /><Where /></>}>
            <Route path="/briefs/*" element={<BriefsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

describe("Briefs board", () => {
  it("groups briefs into status columns and hides cancelled by default", async () => {
    renderAt("/briefs");
    const blocked = await screen.findByRole("region", { name: "Blocked" });
    expect(within(blocked).getByText("REST routes")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "In progress" })).getByText("Build the board")).toBeInTheDocument();
    expect(screen.queryByText("Old idea")).toBeNull();
    await userEvent.click(screen.getByLabelText("Show cancelled"));
    expect(within(await screen.findByRole("region", { name: "Cancelled" })).getByText("Old idea")).toBeInTheDocument();
  });

  it("filters by title and switches to the list view", async () => {
    renderAt("/briefs");
    await screen.findByText("REST routes");
    await userEvent.type(screen.getByLabelText("Filter by title"), "rest");
    expect(screen.queryByText("Build the board")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "List" }));
    expect(await screen.findByRole("table")).toHaveTextContent("REST routes");
    expect(screen.getByTestId("where")).toHaveTextContent("view=list");
  });

  it("opens the detail with the owner in the URL", async () => {
    renderAt("/briefs");
    await userEvent.click(await screen.findByText("Build the board"));
    expect(screen.getByTestId("where")).toHaveTextContent("/briefs/b1?owner=tom");
    expect(await screen.findByRole("heading", { name: "Build the board", level: 1 })).toBeInTheDocument();
  });

  it("creates a brief and lands on it", async () => {
    renderAt("/briefs?new=1");
    const dialog = await screen.findByRole("dialog", { name: "New brief" });
    await userEvent.type(within(dialog).getByLabelText("Title"), "Write the docs");
    await userEvent.type(within(dialog).getByLabelText("Assignee"), "w1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create brief" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/briefs/new1?owner=tom"));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toMatchObject({ user: "tom", title: "Write the docs", assignee: "w1" });
  });

  it("shows the server's message when the list fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ error: "server not ready" }, { status: 503 })));
    renderAt("/briefs");
    expect(await screen.findByRole("alert")).toHaveTextContent("server not ready");
  });
});

describe("Brief detail", () => {
  it("renders the body and the report thread with status transitions", async () => {
    renderAt("/briefs/b1?owner=tom");
    expect(await screen.findByText("board", { selector: "strong" })).toBeInTheDocument();
    const thread = screen.getByTestId("report-thread");
    expect(within(thread).getAllByRole("listitem")).toHaveLength(3);
    expect(within(thread).getByLabelText("status Assigned to In progress")).toBeInTheDocument();
  });

  it("changes status through a confirm and PATCHes with the owner", async () => {
    renderAt("/briefs/b1?owner=tom");
    await userEvent.selectOptions(await screen.findByLabelText("Status"), "blocked");
    const dlg = await screen.findByRole("dialog", { name: "Change status?" });
    await userEvent.click(within(dlg).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ user: "tom", status: "blocked" }));
  });

  it("edits the body with a live preview and saves a new version", async () => {
    renderAt("/briefs/b1?owner=tom");
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const ta = screen.getByLabelText("Body");
    await userEvent.clear(ta);
    await userEvent.type(ta, "## New plan");
    expect(screen.getByRole("heading", { name: "New plan", level: 2 })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save as v3" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ user: "tom", body: "## New plan" }));
  });

  it("diffs the previous version against the current body", async () => {
    renderAt("/briefs/b1?owner=tom&tab=versions");
    const diff = await screen.findByTestId("version-diff");
    expect(diff).toHaveTextContent("- Ship it.");
    expect(diff).toHaveTextContent("+ Ship the **board**.");
  });

  it("finds the owner from the list when the link has none", async () => {
    renderAt("/briefs/b1");
    expect(await screen.findByRole("heading", { name: "Build the board", level: 1 })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/dashboard/api/briefs/b1?user=tom")).toBe(true);
  });

  it("says not found for an unknown id", async () => {
    renderAt("/briefs/nope");
    expect(await screen.findByText("Brief not found")).toBeInTheDocument();
  });
});
