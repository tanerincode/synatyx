import { expect, test } from "@playwright/test";

// Runs against `vite preview` with VITE_MOCK_ALL=1 (see playwright.config.ts).

test("usage: totals, chart, tools and projects, and the window switch", async ({ page }) => {
  await page.goto("./usage");
  await expect(page.getByRole("heading", { name: "Usage", level: 1 })).toBeVisible();

  for (const label of ["Calls", "Context out", "Arguments in", "Embeddings", "Cost"]) {
    await expect(page.locator(".kpi .label", { hasText: new RegExp(`^${label}$`) })).toBeVisible();
  }
  await expect(page.locator(".kpi", { hasText: "Cost" }).locator(".value")).toHaveText(/^\$\d/);

  // 30 days by default: one hit area per day, and hovering reads the day out
  await expect(page.getByRole("button", { name: "30 d" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("day-hit")).toHaveCount(30);
  await page.getByTestId("day-hit").nth(10).hover();
  await expect(page.getByTestId("area-readout")).toContainText("context out");

  // tools are ranked by context tokens, biggest first
  const tools = page.getByTestId("by-tool").locator(".us-tool .mono");
  await expect(tools.first()).toHaveText("retrieve");
  expect(await tools.count()).toBeGreaterThanOrEqual(5);

  // spend by project lists the three projects
  const projects = page.getByRole("table", { name: "Spend by project" });
  await expect(projects.getByRole("row")).toHaveCount(4);
  await expect(projects).toContainText("synatyx");

  // the window lives in the URL and changes the chart
  await page.getByRole("button", { name: "7 d" }).click();
  await expect(page).toHaveURL(/days=7/);
  await expect(page.getByTestId("day-hit")).toHaveCount(7);
  await page.getByRole("button", { name: "30 d" }).click();
  await expect(page).not.toHaveURL(/days=/);
  await expect(page.getByTestId("day-hit")).toHaveCount(30);
});

test("usage: the project filter narrows the spend table", async ({ page }) => {
  await page.goto("./usage?project=kai");
  const projects = page.getByRole("table", { name: "Spend by project" });
  await expect(projects.getByRole("row")).toHaveCount(2);
  await expect(projects).toContainText("kai");
  await expect(projects).not.toContainText("synatyx");
});

test("usage screenshot at 1440 px", async ({ page }) => {
  await page.goto("./usage");
  await expect(page.getByTestId("area-chart")).toBeVisible();
  await page.getByTestId("day-hit").nth(20).hover();
  await page.screenshot({ path: "test-results/shots/usage-1440.png", fullPage: true });
});
