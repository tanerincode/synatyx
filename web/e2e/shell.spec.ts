import { expect, test } from "@playwright/test";

// Runs against `vite preview` with VITE_MOCK_ALL=1 (see playwright.config.ts).
const PAGES = [
  ["Memories", "/dashboard/memories"],
  ["Graph", "/dashboard/graph"],
  ["Briefs", "/dashboard/briefs"],
  ["Tasks", "/dashboard/tasks"],
  ["Skills", "/dashboard/skills"],
  ["Code index", "/dashboard/code"],
  ["Usage", "/dashboard/usage"],
  ["Hygiene", "/dashboard/hygiene"],
  ["Overview", "/dashboard"],
] as const;

test("sidebar reaches every page and keeps the project in the URL", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await page.getByTestId("project-switcher").getByRole("button").click();
  await page.getByRole("option", { name: /synatyx/ }).click();
  await expect(page).toHaveURL(/\?project=synatyx$/);

  const nav = page.getByRole("complementary", { name: "Main navigation" });
  for (const [label, path] of PAGES) {
    await nav.getByRole("link", { name: new RegExp(`^${label}`) }).click();
    await expect(page).toHaveURL(`${path}?project=synatyx`);
    await expect(page.getByRole("heading", { name: label, level: 1 })).toBeVisible();
  }
});

test("deep links survive a reload (SPA fallback) and the palette navigates", async ({ page }) => {
  await page.goto("./usage?project=kai");
  await expect(page.getByRole("heading", { name: "Usage", level: 1 })).toBeVisible();
  await expect(page.getByTestId("project-switcher")).toContainText("kai");
  await page.keyboard.press("ControlOrMeta+k");
  await page.getByRole("textbox", { name: "Command" }).fill("hygiene");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/dashboard\/hygiene\?project=kai$/);
});

test("overview screenshot at 1440 px", async ({ page }) => {
  await page.goto("./?project=synatyx");
  await expect(page.getByText("Blocked brief · Paged /items with q, origin, sort")).toBeVisible();
  await page.screenshot({ path: "e2e/screenshots/overview-1440.png", fullPage: true });
  await page.emulateMedia({ colorScheme: "light" });
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.screenshot({ path: "e2e/screenshots/overview-1440-light.png", fullPage: true });
});
