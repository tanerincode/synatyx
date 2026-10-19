import { expect, test } from "@playwright/test";

// Runs against `vite preview` with VITE_MOCK_ALL=1 (see playwright.config.ts).

test("code index: pick a project, browse the tree, search chunks", async ({ page }) => {
  await page.goto("./code");
  await expect(page.getByRole("heading", { name: "Code index", level: 1 })).toBeVisible();

  const table = page.getByRole("table", { name: "Indexed projects" });
  await expect(table.getByRole("row")).toHaveCount(4); // header + three projects
  await expect(table.getByRole("row", { name: /synatyx/ })).toContainText("python");

  // the first index is selected; its tree and chunks load
  const tree = page.getByRole("tree", { name: "Indexed files" });
  await expect(tree.getByRole("treeitem", { name: /^src/ }).first()).toBeVisible();
  await expect(page.getByTestId("chunk").first()).toBeVisible();
  const all = await page.getByTestId("chunk-count").innerText();

  // directories two levels down start closed; opening one shows its files, closing hides them
  await expect(tree.getByRole("treeitem", { name: /^server\.py/ })).toBeHidden();
  await tree.getByRole("treeitem", { name: /^mcp/ }).click();
  await expect(tree.getByRole("treeitem", { name: /^server\.py/ })).toBeVisible();
  await tree.getByRole("treeitem", { name: /^mcp/ }).click();
  await expect(tree.getByRole("treeitem", { name: /^server\.py/ })).toBeHidden();

  // selecting a file narrows the chunk list to it
  await tree.getByRole("treeitem", { name: /scorer\.py/ }).click();
  await expect(page).toHaveURL(/file=src%2Fcore%2Fscorer\.py/);
  await expect(page.getByTestId("chunk-count")).not.toHaveText(all);
  for (const chunk of await page.getByTestId("chunk").all()) {
    await expect(chunk).toContainText("src/core/scorer.py");
  }

  // the search box filters by path or symbol and shows numbered code
  await page.getByRole("textbox", { name: "Search path or symbol" }).fill("handoff");
  await expect(page).toHaveURL(/q=handoff/);
  await expect(page.getByTestId("chunk").first()).toContainText("handoff");
  await expect(page.getByTestId("chunk").first().locator(".ln span").first()).toHaveText(/^\d+$/);

  // nothing matches: an empty state, not a blank card
  await page.getByRole("textbox", { name: "Search path or symbol" }).fill("zzz-no-such-thing");
  await expect(page.getByRole("heading", { name: "No chunks match" })).toBeVisible();

  // another project replaces the detail
  await table.getByRole("row", { name: /kai/ }).click();
  await expect(page).toHaveURL(/index=ctx_kai__index/);
  await expect(page.getByText("Files · kai")).toBeVisible();
});

test("code index: the project switcher narrows the table, and an unknown project is an empty state", async ({ page }) => {
  await page.goto("./code?project=coverdropai");
  const table = page.getByRole("table", { name: "Indexed projects" });
  await expect(table.getByRole("row")).toHaveCount(2);
  await expect(page.getByText("Files · coverdropai")).toBeVisible();

  await page.goto("./code?project=nope");
  await expect(page.getByRole("heading", { name: "No code index for nope" })).toBeVisible();
});

test("code index screenshot at 1440 px", async ({ page }) => {
  await page.goto("./code?project=synatyx&file=src%2Ftransports");
  await expect(page.getByTestId("chunk").first()).toBeVisible();
  await page.screenshot({ path: "test-results/shots/code-index-1440.png", fullPage: true });
});
