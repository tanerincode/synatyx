import { expect, test } from "@playwright/test";

// Mock store (VITE_MOCK_ALL=1): 8 skills, three of them scoped to a project.
const SHOTS = "test-results/shots";

test("list, search, detail rendered as markdown, copy slug", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("./skills");
  await expect(page.getByRole("heading", { name: "Skills", level: 1 })).toBeVisible();
  const items = page.getByTestId("skill-item");
  await expect(items).toHaveCount(8);

  // the first skill is open by default, its markdown rendered (not shown as raw text)
  const detail = page.getByRole("article");
  await expect(detail.getByRole("heading", { name: "When to use" })).toBeVisible();
  await expect(detail.locator("pre code")).toHaveText("make check");
  await page.screenshot({ path: `${SHOTS}/skills-1440.png`, fullPage: true });

  await page.getByLabel("Search skills").fill("stripe");
  await expect(items).toHaveCount(1);
  await items.first().click();
  await expect(page).toHaveURL(/skill=stripe-webhook-test&owner=tombastaner/);
  await expect(page.getByRole("article", { name: "Stripe webhook test" })).toBeVisible();

  await page.getByRole("button", { name: "Copy slug" }).click();
  await expect(page.getByText("Copied stripe-webhook-test")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("stripe-webhook-test");

  await page.getByLabel("Search skills").fill("zzzz-no-such-skill");
  await expect(page.getByText(/No skill matches/)).toBeVisible();
});

test("the project switcher scopes the list and a deep link opens that skill", async ({ page }) => {
  await page.goto("./skills?project=kai");
  const items = page.getByTestId("skill-item");
  await expect(items.first()).toBeVisible();
  await expect(items.filter({ hasText: "Stripe webhook test" })).toHaveCount(0);
  await expect(items.filter({ hasText: "Brief template" })).toHaveCount(1);

  await page.goto("./skills?skill=pr-review&owner=tombastaner");
  await expect(page.getByRole("article", { name: "PR review checklist" })).toBeVisible();
});
