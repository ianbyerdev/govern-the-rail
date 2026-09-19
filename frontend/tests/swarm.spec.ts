import { test, expect } from "@playwright/test";
import fs from "node:fs";
test.beforeEach(async ({ page }) => {
  const token = fs.readFileSync(".test-token", "utf8");
  await page.goto("/#token=" + token);
  await page.getByRole("button", { name: "Incident / Swarm Lab" }).click();
  await expect(
    page.getByRole("heading", { name: "Many agents. One authority boundary." }),
  ).toBeVisible();
});
async function create(
  page: any,
  scenario: string,
  count = "2",
  posture = "full",
  mode = "replay",
) {
  await page.getByLabel("Scenario", { exact: true }).selectOption(scenario);
  await page.getByLabel("Logical population").selectOption(count);
  await page.getByLabel("Coverage posture").selectOption(posture);
  await page.getByLabel("Execution mode").selectOption(mode);
  await page
    .getByRole("button", { name: "Create experiment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toBeEnabled();
}
async function play(page: any) {
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.locator(".swarm-status")).toHaveText("complete", {
    timeout: 60000,
  });
}
test("useful team, real signed evidence, trace and accessible population", async ({
  page,
}) => {
  await create(page, "team");
  await play(page);
  await expect(page.getByTestId("swarm-budget-messages")).toContainText(
    "1 committed",
  );
  await expect(page.locator(".swarm-story")).toContainText(
    "The protected socket recorded this effect.",
  );
  await page.getByRole("button", { name: "κ", exact: true }).click();
  await expect(page.locator(".swarm-inspector")).toContainText("verified");
  await expect(page.locator(".swarm-json")).toContainText("saac-kappa");
  await page.getByRole("button", { name: "ρ", exact: true }).click();
  await expect(page.locator(".swarm-json")).toContainText("saac-receipt");
  await page.getByRole("button", { name: "Trace", exact: true }).click();
  await expect(page.locator(".swarm-trace")).toContainText(
    "RECEIPT_RECONCILED",
  );
  await page.getByLabel("Actor index").fill("1");
  await expect(page.locator(".swarm-inspector h2")).toContainText("Actor 1");
  await page.screenshot({
    path: "test-results/swarm-team-desktop.png",
    fullPage: true,
  });
});
test("100 distinct actor storm obeys shared collar and stops preserve starts", async ({
  page,
}) => {
  await create(page, "fanout", "100", "full", "concurrent");
  await play(page);
  await expect(page.getByTestId("swarm-budget-agent_slots")).toContainText(
    "64 held",
  );
  await expect(page.locator(".swarm-denials")).toContainText("SESSION_LIMIT");
  await page
    .getByRole("button", { name: "Authorize stops", exact: true })
    .click();
  await expect(page.getByTestId("swarm-budget-agent_slots")).toContainText(
    "0 held",
  );
  await expect(page.getByTestId("swarm-budget-agent_starts")).toContainText(
    "64 committed",
  );
});
test("uncertainty preserves slots after start receipt recovery", async ({
  page,
}) => {
  await create(page, "uncertainty");
  await play(page);
  await expect(page.locator(".swarm-receipt-return")).toContainText(
    "2 uncertain",
  );
  await page.getByRole("button", { name: "Return missing receipts" }).click();
  await expect(page.locator(".swarm-receipt-return")).toContainText(
    "0 uncertain",
  );
  await expect(page.getByTestId("swarm-budget-agent_slots")).toContainText(
    "2 held",
  );
});
test("coverage gap and identical-intent comparison separate oracle from authority", async ({
  page,
}) => {
  await create(page, "coverage", "2", "partial");
  await play(page);
  await expect(page.locator(".swarm-oracle")).toContainText("2 effects");
  await expect(page.getByTestId("swarm-budget-disclosed_bytes")).toContainText(
    "0 committed",
  );
  await page.getByLabel("Coverage posture").selectOption("full");
  await page
    .getByRole("button", { name: "Compare with full coverage" })
    .click();
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toBeEnabled();
  await play(page);
  await expect(page.locator(".swarm-oracle")).toContainText("0 effects");
  await expect(page.locator(".swarm-denials")).toContainText("RESOURCE_DENIED");
});
test("real contained processes and mobile reduced motion", async ({ page }) => {
  await create(page, "team");
  await page
    .getByText("Real contained-worker proof · 2 processes", { exact: true })
    .click();
  await page.getByRole("button", { name: "Run 2 contained workers" }).click();
  await expect(page.locator(".swarm-bottom")).toContainText("IDENTITY_BINDING");
  await expect(page.locator(".swarm-bottom")).toContainText('"returncode": 0');
  await page.setViewportSize({ width: 360, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/swarm-mobile.png",
    fullPage: true,
  });
});

test("staged capability binds exact effect before execution, replay is refused", async ({
  page,
}) => {
  await create(page, "team");
  await page.getByRole("button", { name: "Stage selected κ" }).click();
  await expect(page.getByTestId("swarm-budget-messages")).toContainText(
    "1 held",
  );
  await page
    .getByRole("button", { name: "Modify effect", exact: true })
    .click();
  await expect(page.locator(".swarm-selected-actions")).toContainText(
    "EFFECT_MISMATCH",
  );
  await page.getByRole("button", { name: "Wrong socket", exact: true }).click();
  await expect(page.locator(".swarm-selected-actions")).toContainText(
    "WRONG_AUDIENCE",
  );
  await page
    .getByRole("button", { name: "Present original κ", exact: true })
    .click();
  await expect(page.getByTestId("swarm-budget-messages")).toContainText(
    "1 committed",
  );
  await page
    .getByRole("button", { name: "Attempt replay", exact: true })
    .click();
  await expect(page.locator(".swarm-selected-actions")).toContainText("REPLAY");
});

test("refresh retains the campaign without duplicating effects", async ({
  page,
}) => {
  await create(page, "team");
  await play(page);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Many agents. One authority boundary." }),
  ).toBeVisible();
  await expect(page.getByTestId("swarm-budget-messages")).toContainText(
    "1 committed",
  );
  await expect(page.locator(".swarm-status")).toHaveText("complete");
  await expect(page.locator(".swarm-counts")).toContainText("2κ redeemed");
});
