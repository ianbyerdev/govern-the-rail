import { test, expect } from "@playwright/test";
import fs from "node:fs";

test.beforeEach(async ({ page }) => {
  await page.goto("/#token=" + fs.readFileSync(".test-token", "utf8"));
  await expect(
    page.getByRole("heading", { name: "Pay one exact beneficiary" }),
  ).toBeVisible();
  await expect(page.getByLabel("Integration mode")).toBeEnabled();
  await expect(page).toHaveTitle(/SAAC/);
});

test("authorization retry retains one capability and cannot execute twice", async ({ page }) => {
  await page.getByRole("button", { name: "Authorization retry", exact: true }).click();
  const attempts = page.locator(".demonstration-attempts li");
  await expect(attempts).toHaveCount(3);
  await expect(attempts.nth(0)).toContainText("REUSED");
  await expect(attempts.nth(0)).toContainText("one $1,000 reservation");
  await expect(attempts.nth(1)).toContainText("EXECUTED");
  await expect(attempts.nth(2)).toContainText("REPLAY");
  await page.getByRole("button", { name: "✓ κ ISSUED", exact: true }).click();
  await expect(page.locator(".lifecycle-inspector")).toContainText("saac-kappa");
});

for (const mode of ["native", "harness", "mcp"]) {
  test(`${mode} integration completes every recorded lifecycle stage`, async ({
    page,
  }) => {
    await page.getByLabel("Integration mode").selectOption(mode);
    await page
      .getByRole("button", { name: "Normal authorized action", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Payment recorded" }),
    ).toBeVisible();
    await expect(page.locator(".lifecycle-stages .recorded")).toHaveCount(9);
    await page.getByRole("button", { name: "✓ κ ISSUED", exact: true }).click();
    await expect(page.locator(".lifecycle-inspector")).toContainText(
      "kappa_id",
    );
    await expect(page.locator(".lifecycle-inspector")).toContainText(
      "final_authoritative_risk",
    );
    await expect(page.locator(".lifecycle-inspector")).toContainText(
      "institution.demo",
    );
  });
}

test("bypassing the adapter fails at the same rail that accepts valid authority", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Direct rail bypass", exact: true })
    .click();
  const attempts = page.locator(".demonstration-attempts li");
  await expect(attempts).toHaveCount(2);
  await expect(attempts.nth(0)).toContainText("REJECTED");
  await expect(attempts.nth(0)).toContainText("INVALID_SIGNATURE");
  await expect(attempts.nth(1)).toContainText("EXECUTED");
  await expect(page.getByLabel("Receipt reconciliation")).toContainText(
    "authoritative book updated",
  );
});

test("policy, replay, exact destination mutation and expiry have inspectable refusals", async ({
  page,
}) => {
  for (const [scenario, code] of [
    ["Policy rejection", "ACTION_LIMIT"],
    ["Replay attack", "REPLAY"],
    ["Capability mutation", "EFFECT_MISMATCH"],
    ["Expired capability", "EXPIRED"],
  ]) {
    await page.getByRole("button", { name: scenario, exact: true }).click();
    await expect(
      page.locator(".demonstration-attempts li").last(),
    ).toContainText(code);
    if (scenario !== "Replay attack") {
      await expect(
        page.getByRole("heading", { name: "No effect yet" }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "○ EXECUTED", exact: true }),
      ).toBeDisabled();
    }
  }
});

test("delegated children share lineage and cannot widen scope", async ({
  page,
}) => {
  await page
    .getByRole("button", { name: "Delegation violation", exact: true })
    .click();
  await expect(page.getByLabel("Delegated authority")).toContainText(
    "$10,000.00",
  );
  await expect(page.getByLabel("Delegated authority")).toContainText(
    "$2,000.00",
  );
  await expect(page.getByLabel("Delegated authority")).toContainText(
    "Read-only",
  );
  await expect(page.locator(".demonstration-attempts .refusal")).toHaveCount(4);
  await expect(page.locator(".demonstration-attempts li").last()).toContainText(
    "DELEGATION_EXPANSION",
  );
  await page
    .getByRole("button", { name: "Inspect action 6", exact: true })
    .click();
  await page.getByRole("button", { name: "✓ RESERVED", exact: true }).click();
  await expect(page.locator(".lifecycle-inspector")).toContainText(
    "agent.child-a",
  );
  await expect(page.locator(".lifecycle-context")).toContainText(
    "G-DEMO → grant_",
  );
});

test("selectable naive and atomic swarm modes show tenfold exposure difference", async ({
  page,
}) => {
  await page.getByLabel("Concurrency mode").selectOption("naive");
  await page
    .getByRole("button", { name: "Launch 100 concurrent requests" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Naive policy exceeds the limit" }),
  ).toBeVisible();
  await expect(page.locator(".request-grid .unsafe-admitted")).toHaveCount(100);
  await expect(page.locator(".race-result")).toContainText(
    "0 capabilities · 0 protected effects",
  );
  await expect(page.locator(".race-result")).toContainText(
    "$10,000,000 potential exposure",
  );
  await page.getByLabel("Concurrency mode").selectOption("saac");
  await page
    .getByRole("button", { name: "Launch 100 concurrent requests" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Invariant holds" }),
  ).toBeVisible({ timeout: 60000 });
  await expect(page.locator(".request-grid .accepted")).toHaveCount(10);
  await expect(page.locator(".race-result")).toContainText(
    "$1,000,000 reserved",
  );
  await page.locator(".request-grid .accepted").first().click();
  await expect(
    page.getByRole("button", { name: "✓ RESERVED", exact: true }),
  ).toBeEnabled();
});

test("mobile delegation and stage evidence fit the viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page
    .getByRole("button", { name: "Delegation violation", exact: true })
    .click();
  await expect(page.locator(".demonstration-attempts .refusal")).toHaveCount(4);
  await page.getByRole("button", { name: "✓ CHECKED", exact: true }).click();
  await expect(page.locator(".lifecycle-inspector")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/architecture-mobile.png",
    fullPage: true,
  });
});

test("desktop architecture and bypass visual record", async ({ page }) => {
  await page.getByLabel("Integration mode").selectOption("mcp");
  await page
    .getByRole("button", { name: "Direct rail bypass", exact: true })
    .click();
  await expect(page.locator(".demonstration-attempts li")).toHaveCount(2);
  await page.screenshot({
    path: "test-results/architecture-desktop.png",
    fullPage: true,
  });
});
