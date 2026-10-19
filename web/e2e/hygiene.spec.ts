import { expect, test } from "@playwright/test";

test("hygiene: counts, duplicates and stale list open the shared drawer", async ({ page }) => {
  await page.goto("./hygiene?project=synatyx");
  await expect(page.getByRole("heading", { name: "Hygiene", level: 1 })).toBeVisible();
  for (const label of ["Expiring in 7 d", "Deprecated", "Pending hard delete", "Protected"]) {
    await expect(page.locator(".kpi", { hasText: label })).toBeVisible();
  }
  await expect(page.getByRole("heading", { name: "Duplicate suggestions" })).toBeVisible();
  const dup = page.locator(".dup").first();
  await expect(dup).toBeVisible();
  await expect(dup.locator(".mid")).toContainText(/\d+%|—/);
  await expect(page.getByRole("heading", { name: "Possibly stale" })).toBeVisible();

  await page.screenshot({ path: "test-results/hygiene-1440.png", fullPage: true });

  // a duplicate side opens the drawer; Esc closes it
  await dup.locator(".side").first().click();
  const drawer = page.getByRole("dialog", { name: "Memory detail" });
  await expect(drawer).toBeVisible();
  await expect(page).toHaveURL(/item=/);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  // so does a stale row
  await page.locator(".rel-item").first().click();
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("possibly stale").first()).toBeVisible();
});

test("hygiene: pinning from the drawer reloads the page counts", async ({ page }) => {
  await page.goto("./hygiene?project=synatyx");
  const protectedKpi = page.locator(".kpi", { hasText: "Protected" }).locator(".value");
  await expect(protectedKpi).toHaveText(/\d+/);
  const before = Number(await protectedKpi.textContent());
  await page.locator(".dup .side").first().click();
  const drawer = page.getByRole("dialog", { name: "Memory detail" });
  const pin = drawer.getByRole("button", { name: /^(Pin|Unpin)$/ });
  const wasPin = (await pin.textContent())?.trim() === "Pin";
  await pin.click();
  await expect(page.getByRole("status")).toContainText(wasPin ? "Pinned" : "Unpinned");
  await expect(protectedKpi).toHaveText(String(before + (wasPin ? 1 : -1)));
});
