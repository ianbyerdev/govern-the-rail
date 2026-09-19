import { test, expect } from "@playwright/test";
import fs from "node:fs";

test.beforeEach(async ({ page }) => {
  const token = fs.readFileSync(".test-token", "utf8");
  await page.goto("/#token=" + token);
  await expect(
    page.getByRole("heading", { name: "Pay one exact beneficiary" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Fresh experiment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Resolve & evaluate", exact: true }),
  ).toBeEnabled();
});

async function issue(page: any) {
  await page
    .getByRole("button", { name: "Resolve & evaluate", exact: true })
    .click();
  await expect(page.getByText("κ · Not issued", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Request authority", exact: true })
    .click();
}

test("real preview, reservation, socket and receipt transitions", async ({
  page,
}) => {
  await issue(page);
  await expect(page.getByLabel("Authoritative risk book")).toContainText(
    "$75.00",
  );
  await expect(
    page.getByRole("heading", { name: "No effect yet" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Present κ to socket", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Payment recorded" }),
  ).toBeVisible();
  await expect(page.getByLabel("Receipt reconciliation")).toContainText(
    "awaiting reconciliation",
  );
  await page
    .getByRole("button", { name: "Reconcile signed ρ", exact: true })
    .click();
  await expect(page.getByLabel("Receipt reconciliation")).toContainText(
    "authoritative book updated",
  );
  await page.locator(".evidence > summary").click();
  await page.getByRole("tab", { name: "κ Capability" }).click();
  await expect(page.locator(".json-view")).toContainText("saac-kappa");
  await page.getByRole("tab", { name: "ρ Receipt" }).click();
  await expect(page.locator(".json-view")).toContainText("saac-receipt");
  await page.getByRole("button", { name: "Verify complete tape" }).click();
  await expect(page.locator(".tape-tools")).toContainText("Verified");
  await page.getByRole("button", { name: "Attempt replay" }).click();
  await expect(page.locator(".socket-decision")).toContainText("REPLAY");
});

test("modification fails before redemption; original authority remains usable", async ({
  page,
}) => {
  await issue(page);
  await page.getByLabel("Attack / failure condition").selectOption("tamper");
  await page.getByRole("button", { name: "Present κ to socket" }).click();
  await expect(page.locator(".socket-decision")).toContainText(
    "EFFECT_MISMATCH",
  );
  await expect(
    page.getByRole("heading", { name: "No effect yet" }),
  ).toBeVisible();
  await page.getByLabel("Attack / failure condition").selectOption("none");
  await page.getByRole("button", { name: "Present κ to socket" }).click();
  await expect(
    page.getByRole("heading", { name: "Payment recorded" }),
  ).toBeVisible();
});

test("lost receipt retains risk until recovery", async ({ page }) => {
  await issue(page);
  await page.getByLabel("Attack / failure condition").selectOption("lost");
  await page.getByRole("button", { name: "Present κ to socket" }).click();
  await expect(page.getByLabel("Receipt reconciliation")).toContainText(
    "capacity remains held",
  );
  await expect(page.getByTestId("budget-amount_cents")).toContainText("$75.00");
  await page.getByRole("button", { name: "Recover signed ρ" }).click();
  await expect(page.getByLabel("Receipt reconciliation")).toContainText(
    "authoritative book updated",
  );
});

test("human approval binds the reviewed payment", async ({ page }) => {
  await page.getByRole("button", { name: "Human review experiment" }).click();
  await expect(
    page.getByRole("heading", { name: "Approve this effect only" }),
  ).toBeVisible();
  await expect(page.locator(".review-grid")).toContainText("$150.00");
  await page.getByRole("button", { name: "Approve exact snapshot" }).click();
  await page.getByLabel("Attack / failure condition").selectOption("tamper");
  await page.getByRole("button", { name: "Present κ to socket" }).click();
  await expect(page.locator(".socket-decision")).toContainText(
    "EFFECT_MISMATCH",
  );
});

test("100-way race is isolated and admits exactly ten capabilities", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Launch 100 concurrent requests" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Invariant holds" }),
  ).toBeVisible({ timeout: 60000 });
  await expect(page.locator(".request-grid .accepted")).toHaveCount(10);
  await expect(page.locator(".race-result")).toContainText(
    "90 denied at reservation",
  );
  await expect(page.locator(".unsafe-comparison")).toContainText(
    "90 units oversubscribed",
  );
});

test("trading acceptance, partial fill, separately authorized cancellation", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Trading OMS / EMS", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Fresh experiment", exact: true })
    .click();
  await page.getByRole("button", { name: "Run normal action" }).click();
  await expect(
    page.getByRole("heading", { name: "working · 0 / 2,000 filled" }),
  ).toBeVisible();
  await expect(page.getByTestId("budget-notional_usd_cents")).toContainText(
    "$82,540.00",
  );
  await page.getByRole("button", { name: "Simulate 400-share fill" }).click();
  await expect(page.getByTestId("budget-notional_usd_cents")).toContainText(
    "$66,032.00",
  );
  await page
    .getByRole("button", { name: "Authorize cancel remainder" })
    .click();
  await expect(
    page.getByRole("heading", { name: "cancelled · 400 / 2,000 filled" }),
  ).toBeVisible();
  await expect(page.getByTestId("budget-notional_usd_cents")).toContainText(
    "$16,508.00",
  );
  await expect(page.getByText("Inspect separate cancellation κ")).toBeVisible();
});

test("referrals require manifest review and fail after consent revocation", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Patient referrals", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Fresh experiment", exact: true })
    .click();
  await page.getByRole("button", { name: "Run normal action" }).click();
  await expect(
    page.getByRole("heading", { name: "Approve this effect only" }),
  ).toBeVisible();
  await expect(page.locator(".manifest")).toContainText("doc-6");
  await page.getByRole("button", { name: "Approve exact snapshot" }).click();
  await page.getByLabel("Attack / failure condition").selectOption("consent");
  await page.getByRole("button", { name: "Present κ to socket" }).click();
  await expect(page.locator(".socket-decision")).toContainText("STALE_STATE");
  await expect(
    page.getByRole("heading", { name: "No effect yet" }),
  ).toBeVisible();
});

test("runtime runs real contained probes and exposes the coverage gap", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Agent runtime", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Fresh experiment", exact: true })
    .click();
  await page.getByRole("button", { name: "Run normal action" }).click();
  await expect(
    page.getByRole("heading", { name: "Constrained audit completed" }),
  ).toBeVisible();
  await expect(page.locator(".probe-results")).toContainText("Read risk book");
  await expect(page.locator(".probe-results > div")).toHaveCount(4);
  await page.getByRole("button", { name: "Call socket without κ" }).click();
  await expect(page.locator(".coverage-result")).toContainText(
    "Attempt blocked",
  );
  await page
    .getByRole("button", { name: "Try isolated unprotected sink" })
    .click();
  await expect(page.locator(".coverage-result")).toContainText(
    "Bypass succeeds",
  );
  await expect(page.locator(".coverage-result")).toContainText(
    "No enforcement",
  );
});

test("360px layout retains readable controls without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await expect(
    page.getByRole("button", { name: "Resolve & evaluate", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await issue(page);
  await page.getByLabel("Attack / failure condition").selectOption("tamper");
  await page.getByRole("button", { name: "Present κ to socket" }).click();
  await expect(page.locator(".socket-decision")).toContainText(
    "EFFECT_MISMATCH",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
