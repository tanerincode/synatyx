import { expect, test } from "@playwright/test";

// Mock store (VITE_MOCK_ALL=1): 15 tasks across kai / coverdropai / synatyx, owned by "tombastaner".
const SHOTS = "test-results/shots";

test("filter, edit inline and switch to the board", async ({ page }) => {
  await page.goto("./tasks");
  await expect(page.getByRole("heading", { name: "Tasks", level: 1 })).toBeVisible();
  const rows = page.getByTestId("task-row");
  await expect(rows.first()).toBeVisible();
  const visible = await rows.count();
  await page.screenshot({ path: `${SHOTS}/tasks-table-1440.png`, fullPage: true });

  // cancelled tasks are hidden until their chip is switched on
  await page.getByRole("button", { name: "Cancelled" }).click();
  await expect(page).toHaveURL(/status=pending%2Cin_progress%2Cdone%2Ccancelled|status=pending,in_progress,done,cancelled/);
  await expect(rows).toHaveCount(visible + 1);

  await page.getByLabel("Priority filter").selectOption("high");
  await expect(page).toHaveURL(/priority=high/);
  const high = await rows.count();
  expect(high).toBeGreaterThan(0);
  expect(high).toBeLessThan(visible + 1);
  for (const row of await rows.all()) await expect(row.getByLabel(/Priority of/)).toHaveValue("high");

  // inline status edit: the row changes at once and the change survives a refresh
  const first = rows.first();
  const title = (await first.locator("td").first().innerText()).trim();
  await page.getByLabel(`Status of ${title}`).selectOption("done");
  await expect(page.getByLabel(`Status of ${title}`)).toHaveValue("done");
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByLabel(`Status of ${title}`)).toHaveValue("done");

  await page.getByRole("button", { name: "Board" }).click();
  await expect(page).toHaveURL(/view=board/);
  const done = page.getByRole("region", { name: "Done" });
  await expect(done.getByText(title)).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/tasks-board-1440.png`, fullPage: true });
});

// A failing PATCH (rollback plus toast) is covered in src/pages/tasks/useTaskEdits.test.tsx:
// with VITE_MOCK_ALL the client never reaches fetch, so there is nothing to intercept here.

test("the project switcher and the assignee filter narrow the table", async ({ page }) => {
  await page.goto("./tasks?project=coverdropai");
  const rows = page.getByTestId("task-row");
  await expect(rows.first()).toBeVisible();
  for (const row of await rows.all()) await expect(row).toContainText("coverdropai");

  await page.getByLabel("Assignee filter").selectOption("__unassigned__");
  await expect(page).toHaveURL(/assignee=__unassigned__/);
  for (const row of await rows.all()) await expect(row.getByLabel(/Assignee of/)).toHaveValue("");
});
