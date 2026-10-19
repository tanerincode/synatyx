import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../../components/ui";
import { ItemDrawer } from "./ItemDrawer";

const ID = "11111111-1111-1111-1111-111111111111";
const detail = (over: Record<string, unknown> = {}) => ({
  id: ID, content: "Redis runs on 6379", memory_layer: "L3", importance: 0.8, is_pinned: false, is_deprecated: false,
  origin: "user-stated", type: null, user_id: "tom", session_id: null, project: "synatyx", created_at: "2026-10-01T10:00:00Z",
  confidence: 0.9, fact_type: "config", files: ["a.py"], possibly_stale: true, stale_files: ["a.py"],
  related: [{ id: "22222222-2222-2222-2222-222222222222", type: "supersedes", direction: "in", label: "Redis moved to 6380" }], ...over,
});

let posts: { url: string; body: string | undefined }[] = [];
function stubFetch(actionStatus: number, after: Record<string, unknown>) {
  posts = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push({ url, body: init.body as string | undefined });
      return actionStatus === 200
        ? new Response(JSON.stringify(detail(after)), { status: 200 })
        : new Response(JSON.stringify({ error: "boom" }), { status: actionStatus });
    }
    return new Response(JSON.stringify(detail()), { status: 200 });
  }));
}
afterEach(() => vi.unstubAllGlobals());

function setup(onChanged = vi.fn(), onClose = vi.fn()) {
  render(
    <MemoryRouter>
      <ToastProvider>
        <ItemDrawer itemId={ID} collection="ctx_synatyx" onClose={onClose} onChanged={onChanged} />
      </ToastProvider>
    </MemoryRouter>,
  );
  return { onChanged, onClose };
}

describe("ItemDrawer", () => {
  it("shows the detail fields, stale files and related edges", async () => {
    stubFetch(200, {});
    setup();
    expect(await screen.findByText("Redis runs on 6379")).toBeInTheDocument();
    expect(screen.getByText("changed since store")).toBeInTheDocument();
    expect(screen.getByText("possibly stale")).toBeInTheDocument();
    expect(screen.getByText("Superseded by")).toBeInTheDocument();
    expect(screen.getAllByText("Redis moved to 6380").length).toBeGreaterThan(0);
  });

  it("pins optimistically, posts the action and tells the caller", async () => {
    stubFetch(200, { is_pinned: true });
    const { onChanged } = setup();
    await userEvent.click(await screen.findByRole("button", { name: /^Pin/ }));
    expect(await screen.findByRole("button", { name: /^Unpin/ })).toBeInTheDocument();
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(posts[0]?.url).toContain(`/items/${ID}/pin?collection=ctx_synatyx`);
    expect(await screen.findByRole("status")).toHaveTextContent("Pinned 11111111");
  });

  it("rolls back and explains when the server refuses", async () => {
    stubFetch(500, {});
    const { onChanged } = setup();
    await userEvent.click(await screen.findByRole("button", { name: /^Pin/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Couldn't pin 11111111: boom");
    expect(screen.getByRole("button", { name: /^Pin/ })).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("deprecates with a reason", async () => {
    stubFetch(200, { is_deprecated: true, deprecated_reason: "outdated" });
    setup();
    await userEvent.click(await screen.findByRole("button", { name: /^Deprecate/ }));
    await userEvent.type(screen.getByLabelText(/Reason/), "outdated");
    await userEvent.click(screen.getAllByRole("button", { name: "Deprecate" }).at(-1) as HTMLElement);
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.url).toContain("/deprecate?collection=ctx_synatyx");
    expect(JSON.parse(posts[0]?.body ?? "{}")).toEqual({ reason: "outdated" });
    expect(await screen.findByRole("button", { name: /^Restore/ })).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    stubFetch(200, {});
    const { onClose } = setup();
    await screen.findByText("Redis runs on 6379");
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
