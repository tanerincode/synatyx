import { expect, test, type Page } from "@playwright/test";

// Runs against `vite preview` with VITE_MOCK_ALL=1 (see playwright.config.ts).

async function counts(page: Page): Promise<{ nodes: number; edges: number }> {
  const text = await page.getByTestId("graph-stats").innerText();
  const m = /(\d+) nodes · (\d+) edges/.exec(text);
  if (!m) throw new Error(`unexpected stats: ${text}`);
  return { nodes: Number(m[1]), edges: Number(m[2]) };
}

async function settled(page: Page): Promise<void> {
  await expect(page.getByTestId("graph-shell")).toHaveAttribute("data-ready", "1");
}

test("graph: filters change what is drawn", async ({ page }) => {
  await page.goto("./graph?project=synatyx");
  await expect(page.getByRole("heading", { name: "Graph", level: 1 })).toBeVisible();
  await settled(page);
  const all = await counts(page);
  expect(all.nodes).toBeGreaterThan(20);
  expect(all.edges).toBeGreaterThan(10);

  // a layer off removes its nodes
  await page.getByRole("button", { name: "L1", exact: true }).click();
  await expect(page.getByRole("button", { name: "L1", exact: true })).toHaveAttribute("aria-pressed", "false");
  await expect.poll(async () => (await counts(page)).nodes).toBeLessThan(all.nodes);
  await page.getByRole("button", { name: "L1", exact: true }).click();
  await expect.poll(async () => (await counts(page)).nodes).toBe(all.nodes);

  // a relation type off removes edges but no nodes
  await page.getByRole("group", { name: "Relation types" }).getByRole("button", { name: "related_to" }).click();
  await expect.poll(async () => (await counts(page)).edges).toBeLessThan(all.edges);
  expect((await counts(page)).nodes).toBe(all.nodes);
  await page.getByRole("group", { name: "Relation types" }).getByRole("button", { name: "related_to" }).click();
  await expect.poll(async () => (await counts(page)).edges).toBe(all.edges);

  // connected only drops the loose nodes; deprecated off drops the faded ones
  await page.getByLabel("Connected only").check();
  await expect.poll(async () => (await counts(page)).nodes).toBeLessThan(all.nodes);
  await page.getByLabel("Connected only").uncheck();
  await page.getByLabel("Deprecated").uncheck();
  await expect.poll(async () => (await counts(page)).nodes).toBeLessThan(all.nodes);
});

test("graph: find a node, centre on it, click it to open the shared drawer", async ({ page }) => {
  await page.goto("./graph?project=synatyx");
  await settled(page);

  await page.getByRole("textbox", { name: "Find node" }).fill("zzz-nothing-like-this");
  await page.keyboard.press("Enter");
  await expect(page.getByText("No node matches")).toBeVisible();

  await page.getByRole("textbox", { name: "Find node" }).fill("handoff-model-v1");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/focus=/);
  const id = new URL(page.url()).searchParams.get("focus") ?? "";
  await expect(page.getByTestId("graph-shell")).toHaveAttribute("data-focused", id);

  // the focused node is now at the centre of the canvas
  await page.getByTestId("graph-shell").click();
  await expect(page).toHaveURL(new RegExp(`item=${id}`));
  const drawer = page.getByRole("dialog", { name: "Memory detail" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("handoff-model-v1");
  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/item=/);
});

test("graph: a ?focus= deep link centres on that node (the drawer's Show in graph)", async ({ page }) => {
  await page.goto("./graph?project=synatyx");
  await settled(page);
  await page.getByRole("textbox", { name: "Find node" }).fill("collection-per-project");
  await page.keyboard.press("Enter");
  const id = new URL(page.url()).searchParams.get("focus") ?? "";
  expect(id).not.toBe("");

  await page.goto(`./graph?project=synatyx&focus=${id}`);
  await expect(page.getByTestId("graph-shell")).toHaveAttribute("data-focused", id);
  await page.getByRole("button", { name: "Clear focus" }).click();
  await expect(page).not.toHaveURL(/focus=/);
});

test("graph: hovering a node shows its card", async ({ page }) => {
  await page.goto("./graph?project=synatyx");
  await settled(page);
  await page.getByRole("textbox", { name: "Find node" }).fill("handoff-model-v1");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("graph-shell")).not.toHaveAttribute("data-focused", "");
  const box = await page.getByTestId("graph-shell").boundingBox();
  if (!box) throw new Error("no canvas");
  await page.mouse.move(box.x + box.width / 2 + 2, box.y + box.height / 2 + 2);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const tip = page.getByTestId("graph-tooltip");
  await expect(tip).toBeVisible();
  await expect(tip).toContainText("click to open");
});

test("graph: a project with no memories gets an empty state", async ({ page }) => {
  await page.goto("./graph?project=nope");
  await expect(page.getByRole("heading", { name: "No memories to draw" })).toBeVisible();
});

test("graph screenshots at 1440 px", async ({ page }) => {
  await page.goto("./graph?project=synatyx");
  await settled(page);
  await page.waitForTimeout(600); // let the fit-to-screen move finish
  await page.screenshot({ path: "test-results/shots/graph-fit-1440.png", fullPage: true });
  await page.getByRole("textbox", { name: "Find node" }).fill("handoff-model-v1");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("graph-shell")).not.toHaveAttribute("data-focused", "");
  const box = await page.getByTestId("graph-shell").boundingBox();
  if (!box) throw new Error("no canvas");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(page.getByTestId("graph-tooltip")).toBeVisible();
  await page.screenshot({ path: "test-results/shots/graph-1440.png", fullPage: true });
});
