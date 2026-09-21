import { test, expect } from "@playwright/test";

async function start(page: import("@playwright/test").Page) {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Govern The Rail Lab", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Start demo", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Pay one exact beneficiary" }),
  ).toBeVisible();
  await expect(page.getByLabel("Session controls")).toContainText(
    "Private demo",
  );
}

test("public entry creates a private workspace without exposing credentials", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Agentic RISC · Govern The Rail Lab");
  await expect(
    page.getByText("Agentic RISC · Independent institutional controls", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start demo", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByLabel("Operator credential", { exact: true }),
  ).not.toBeVisible();
  await page.screenshot({
    path: "test-results/public-entry-desktop.png",
    fullPage: true,
  });
  await start(page);
  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === "saac_demo")!;
  expect(session.httpOnly).toBe(true);
  expect(session.sameSite).toBe("Strict");
  expect(await page.evaluate(() => document.cookie)).not.toContain("saac_demo");
  expect(
    await page.evaluate(() => sessionStorage.getItem("saac-operator")),
  ).toBeNull();
  await page
    .getByRole("button", { name: "Direct rail bypass", exact: true })
    .click();
  await expect(
    page.locator(".demonstration-attempts li").first(),
  ).toContainText("INVALID_SIGNATURE");
  await expect(page.locator(".demonstration-attempts li").last()).toContainText(
    "EXECUTED",
  );
  const selected = await page.evaluate(() =>
    sessionStorage.getItem("saac-book"),
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Payment recorded", exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("saac-book"))).toBe(
    selected,
  );
  await page.getByRole("button", { name: "End demo", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Start demo", exact: true }),
  ).toBeVisible();
  expect((await context.cookies()).some((c) => c.name === "saac_demo")).toBe(
    false,
  );
});

test("two browsers get separate books and cannot inspect each other's actions", async ({
  page,
  browser,
}) => {
  await start(page);
  await page
    .getByRole("button", { name: "Normal authorized action", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Payment recorded", exact: true }),
  ).toBeVisible();
  const firstBook = await page.evaluate(() =>
    sessionStorage.getItem("saac-book"),
  );
  const other = await browser.newContext();
  try {
    const second = await other.newPage();
    await start(second);
    await expect(
      second.getByRole("heading", { name: "No effect yet", exact: true }),
    ).toBeVisible();
    const result = await second.request.get(
      "/api/demo/workbench/books/" + firstBook,
    );
    expect(result.status()).toBe(409);
    expect((await second.request.get("/api/operator/state")).status()).toBe(
      401,
    );
    await second.getByRole("button", { name: "End demo", exact: true }).click();
  } finally {
    await other.close();
  }
  await page.getByRole("button", { name: "End demo", exact: true }).click();
});

test("public swarm and runtime clearly show their execution limits", async ({
  page,
}) => {
  await start(page);
  await page
    .getByRole("button", { name: "Agent runtime", exact: true })
    .click();
  await expect(
    page.getByText("Public demos show job authorization and delegation.", {
      exact: false,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run normal action", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Incident / Swarm Lab", exact: true })
    .click();
  await expect(
    page.getByLabel("Logical population").locator("option"),
  ).toHaveCount(2);
  await page.getByLabel("Logical population").selectOption("2");
  await page
    .getByRole("button", { name: "Create experiment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(page.locator(".swarm-status")).toContainText("complete");
  await page
    .getByText("Real contained-worker proof · 2 processes", { exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Run 2 contained workers", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "End demo", exact: true }).click();
});

test("expired or missing sessions return to a usable start page", async ({
  page,
  context,
}) => {
  await start(page);
  await context.clearCookies();
  await page
    .getByRole("button", { name: "Normal authorized action", exact: true })
    .click();
  await expect(
    page.getByText("Your session has ended. Start a new demo to continue.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start demo", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Start demo", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Pay one exact beneficiary" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "End demo", exact: true }).click();
});

test("public entry and private workspace fit a phone", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Start demo", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "test-results/public-entry-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await start(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "End demo", exact: true }).click();
});
