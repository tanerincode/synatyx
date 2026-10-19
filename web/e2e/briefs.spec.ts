import { expect, test } from "@playwright/test";

// Mock store (VITE_MOCK_ALL=1): 9 briefs; "Paged /items with q, origin, sort" is blocked,
// "Dashboard redesign — build the Vite app" is in progress with a v1 → v2 history.
const SHOTS = "e2e/screenshots";

test("kanban → detail → edit → versions", async ({ page }) => {
  await page.goto("./briefs");
  const blocked = page.getByRole("region", { name: "Blocked" });
  await expect(blocked.getByTestId("brief-card")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Cancelled" })).toHaveCount(0);
  await page.screenshot({ path: `${SHOTS}/briefs-board-1440.png`, fullPage: true });

  await page.getByRole("region", { name: "In progress" }).getByText("Dashboard redesign — build the Vite app").click();
  await expect(page).toHaveURL(/\/dashboard\/briefs\/[0-9a-f-]+\?owner=tombastaner$/);
  await expect(page.getByRole("heading", { name: "Dashboard redesign — build the Vite app", level: 1 })).toBeVisible();
  const thread = page.getByTestId("report-thread");
  await expect(thread.getByRole("listitem")).toHaveCount(4);
  await expect(thread.getByLabel("status Assigned to In progress")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/brief-detail-1440.png`, fullPage: true });

  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Body").fill("# Dashboard build\n\nShell merged. **Briefs** page next.");
  await expect(page.locator(".editor .md strong")).toHaveText("Briefs");
  await page.screenshot({ path: `${SHOTS}/brief-edit-1440.png`, fullPage: true });
  await page.getByRole("button", { name: "Save as v3" }).click();
  await expect(page.getByText("v3", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: /Versions/ }).click();
  await expect(page.getByTestId("version-diff")).toContainText("+ Shell merged. **Briefs** page next.");
});

test("status change goes through a confirm", async ({ page }) => {
  await page.goto("./briefs");
  await page.getByText("Write the roster doc for Kai Bridge").click();
  await page.getByLabel("Status", { exact: true }).selectOption("in_progress");
  await page.getByRole("dialog", { name: "Change status?" }).getByRole("button", { name: "Save" }).click();
  await expect(page.locator(".bd-meta .status")).toHaveText("In progress");
  await page.getByRole("link", { name: "Back to briefs" }).click();
  await expect(page.getByRole("region", { name: "In progress" }).getByText("Write the roster doc for Kai Bridge")).toBeVisible();
});

test("new brief from the palette lands on its detail", async ({ page }) => {
  await page.goto("./?project=synatyx");
  await expect(page.getByRole("heading", { name: "Overview", level: 1 })).toBeVisible();
  await page.keyboard.press("ControlOrMeta+k");
  await page.getByRole("textbox", { name: "Command" }).fill("new brief");
  await page.keyboard.press("Enter");
  const dlg = page.getByRole("dialog", { name: "New brief" });
  await dlg.getByLabel("Title", { exact: true }).fill("Serve web/dist at /dashboard");
  await dlg.getByLabel("Assignee").fill("synatyx-w4");
  await page.screenshot({ path: `${SHOTS}/brief-new-1440.png` });
  await dlg.getByRole("button", { name: "Create brief" }).click();
  await expect(page.getByRole("heading", { name: "Serve web/dist at /dashboard", level: 1 })).toBeVisible();
  await expect(page.locator(".bd-meta .status")).toHaveText("Assigned");
});

test("list view, light theme and 768 px", async ({ page }) => {
  await page.goto("./briefs?view=list");
  await expect(page.getByRole("table")).toContainText("Paged /items with q, origin, sort");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.screenshot({ path: `${SHOTS}/briefs-list-1440-light.png`, fullPage: true });
  await page.getByRole("button", { name: "Board" }).click();
  await page.screenshot({ path: `${SHOTS}/briefs-board-1440-light.png`, fullPage: true });
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.screenshot({ path: `${SHOTS}/briefs-board-768.png`, fullPage: true });
  await page.getByText("Hygiene endpoint (/dashboard/api/hygiene)").click();
  await expect(page.getByTestId("report-thread")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/brief-detail-768.png`, fullPage: true });
});
