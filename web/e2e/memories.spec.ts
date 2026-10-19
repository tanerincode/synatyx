import { expect, test } from "@playwright/test";

// Runs against `vite preview` with VITE_MOCK_ALL=1 (see playwright.config.ts).
test("memories: filter, sort, page, open the drawer and pin", async ({ page }) => {
  await page.goto("./memories?project=synatyx");
  await expect(page.getByRole("heading", { name: "Memories", level: 1 })).toBeVisible();
  const rows = page.locator("#mem-table tbody tr[data-id]");
  await expect(rows).toHaveCount(15);
  await expect(page.getByText(/Showing 1–15 of \d+/)).toBeVisible();

  // paging keeps the page in the URL
  await page.getByRole("button", { name: /Next/ }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText(/Showing 16–/)).toBeVisible();
  await page.getByRole("button", { name: /Prev/ }).click();

  // layer chip + search narrow the list; sorting flips the arrow
  await page.getByRole("button", { name: /^L3/ }).click();
  await expect(page).toHaveURL(/layer=L3/);
  await expect(rows.first().locator(".pill")).toContainText("L3");
  await page.getByRole("columnheader", { name: /Importance/ }).click();
  await expect(page).toHaveURL(/sort=importance/);
  await expect(page.getByRole("columnheader", { name: /Importance/ })).toHaveAttribute("aria-sort", "descending");

  await page.getByLabel("Search memories").fill("zzz-no-such-memory");
  await expect(page.getByText("No memories match these filters")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(rows.first()).toBeVisible();

  await page.screenshot({ path: "test-results/memories-list-1440.png" });

  // drawer: detail, pin round trip with a toast, Esc closes
  await rows.first().click();
  const drawer = page.getByRole("dialog", { name: "Memory detail" });
  await expect(drawer).toBeVisible();
  await expect(page).toHaveURL(/item=/);
  await expect(drawer.getByText("Metadata")).toBeVisible();
  const pin = drawer.getByRole("button", { name: /^(Pin|Unpin)$/ });
  const before = (await pin.textContent())?.trim();
  await pin.click();
  await expect(page.getByRole("status")).toContainText(before === "Pin" ? "Pinned" : "Unpinned");
  await expect(drawer.getByRole("button", { name: before === "Pin" ? "Unpin" : "Pin" })).toBeVisible();

  await page.screenshot({ path: "test-results/memories-drawer-1440.png" });
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(page).not.toHaveURL(/item=/);
});

test("memories: deprecate with a reason, then restore", async ({ page }) => {
  await page.goto("./memories?project=synatyx");
  await page.locator("#mem-table tbody tr[data-id]").first().click();
  const drawer = page.getByRole("dialog", { name: "Memory detail" });
  await drawer.getByRole("button", { name: "Deprecate" }).click();
  await page.getByLabel(/Reason/).fill("outdated");
  await page.getByRole("dialog", { name: "Deprecate memory" }).getByRole("button", { name: "Deprecate" }).click();
  await expect(page.getByRole("status")).toContainText("Deprecated");
  await expect(drawer.getByRole("button", { name: "Restore" })).toBeVisible();
  await drawer.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByRole("status")).toContainText("Restored");
  await expect(drawer.getByRole("button", { name: "Deprecate" })).toBeVisible();
});

test("memories: stale filter and a shareable drawer link", async ({ page }) => {
  await page.goto("./memories?project=synatyx&stale=1");
  const rows = page.locator("#mem-table tbody tr[data-id]");
  await expect(rows.first()).toBeVisible();
  for (const tag of await rows.locator(".tag.warn").allTextContents()) expect(tag).toContain("possibly stale");
  await expect(rows.first().locator(".tag.warn")).toContainText("possibly stale");
  const id = await rows.first().getAttribute("data-id");
  await page.goto(`./memories?project=synatyx&item=${id}`);
  await expect(page.getByRole("dialog", { name: "Memory detail" }).getByText(id as string)).toBeVisible();
});
