import {
  test,
  expect as baseExpect,
  type Locator,
  type Page,
} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const expect = baseExpect.configure({ timeout: 45000 });
test.setTimeout(120000);

async function openExperiment(page: Page, visitor = false) {
  await page.goto(
    visitor ? "/" : "/#token=" + fs.readFileSync(".test-token", "utf8"),
  );
  if (visitor)
    await page.getByRole("button", { name: "Start demo", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Pay one exact beneficiary" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Missing receipt experiment", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "A missing receipt does not create capacity",
    }),
  ).toBeVisible();
  await expect(page.locator(".uncertain-feedback")).not.toHaveText(
    "Loading experiment",
  );
}

function panels(page: Page) {
  return {
    evidence: page.getByRole("article", {
      name: "Evidence-based policy",
      exact: true,
    }),
    timeout: page.getByRole("article", {
      name: "Release on timeout policy",
      exact: true,
    }),
  };
}

async function expectBook(
  panel: Locator,
  consumed: number,
  reserved: number,
  available: number,
) {
  for (const [metric, value] of Object.entries({
    limit: 10,
    consumed,
    reserved,
    available,
  })) {
    await expect(panel.locator(`[data-metric="${metric}"] dd`)).toHaveText(
      String(value),
    );
  }
}

async function step(page: Page, label: string) {
  await page
    .getByRole("button", { name: "Step: " + label, exact: true })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Current experiment frame" })
      .getByRole("heading", { name: label, exact: true }),
  ).toBeVisible();
}

function evidenceScreenshot(name: string) {
  const folder = process.env.SAAC_EVIDENCE_DIR;
  if (folder) fs.mkdirSync(folder, { recursive: true });
  return folder ? path.join(folder, name) : test.info().outputPath(name);
}

test("uncertain execution exposes matched admission, deadline divergence and evidence recovery", async ({
  page,
}) => {
  await openExperiment(page);
  // Operator experiments survive test cases; resetting creates an isolated book pair.
  const reset = page.getByRole("button", {
    name: "Reset to new experiment",
    exact: true,
  });
  if (await reset.isEnabled()) await reset.click();
  await step(page, "Initial admission");
  const { evidence, timeout } = panels(page);
  for (const panel of [evidence, timeout]) {
    await expectBook(panel, 0, 10, 0);
    await expect(
      panel.locator(".uncertain-admissions > div").first(),
    ).toContainText("100 submitted");
    await expect(
      panel.locator(".uncertain-admissions > div").first(),
    ).toContainText("10 admitted · 90 denied");
    await expect(panel).toContainText("10 valid unredeemed promises");
    await expect(panel.locator(".uncertain-total")).toContainText("10 units");
  }
  await step(page, "Working orders & hidden fills");
  for (const panel of [evidence, timeout]) {
    await expectBook(panel, 0, 10, 0);
    await expect(panel.locator("[data-metric=obligation]")).toHaveText(
      "10 units",
    );
    await expect(panel).toContainText("4 executed + 6 live order commitments");
  }
  await step(page, "Deadline");
  await expectBook(evidence, 0, 10, 0);
  await expectBook(timeout, 0, 0, 10);
  await evidence.getByText(/Exception queue ·/).click();
  await expect(evidence.locator("ul.uncertain-exceptions li")).toHaveCount(10);
  await expect(
    evidence.locator("ul.uncertain-exceptions li").first(),
  ).toContainText("Next action:");
  await evidence.getByText(/Exception queue ·/).click();
  await step(page, "Second batch");
  await expectBook(evidence, 0, 10, 0);
  await expectBook(timeout, 0, 10, 0);
  await expect(
    evidence.locator(".uncertain-admissions > div").last(),
  ).toContainText("0 admitted · 10 denied");
  await expect(
    timeout.locator(".uncertain-admissions > div").last(),
  ).toContainText("10 admitted · 0 denied");
  await expect(evidence.locator("[data-metric=obligation]")).toHaveText(
    "10 units",
  );
  await expect(timeout.locator("[data-metric=obligation]")).toHaveText(
    "20 units",
  );
  await expect(timeout).toContainText("10 units above the ceiling");
  await step(page, "Recovered fills");
  await expectBook(evidence, 4, 6, 0);
  await step(page, "Confirmed cancellations");
  await expectBook(evidence, 4, 0, 6);
  await expect(timeout).toContainText("Unaccounted liability:");
  await expect(timeout.locator("[data-metric=obligation]")).toHaveText(
    "14 units",
  );
  await expect(
    page.getByRole("button", { name: "All stages complete" }),
  ).toBeDisabled();
  await page.screenshot({
    path: evidenceScreenshot("uncertainty-desktop.png"),
    fullPage: true,
  });

  const mutations: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("/uncertain"))
      mutations.push(request.url());
  });
  await page
    .getByRole("button", { name: "Replay Second batch", exact: true })
    .click();
  await expect(
    page.getByText("Replay · recorded backend frame", { exact: true }),
  ).toBeVisible();
  await expect(timeout.locator("[data-metric=obligation]")).toHaveText(
    "20 units",
  );
  await page.screenshot({
    path: evidenceScreenshot("uncertainty-checkpoint-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Return to latest result" }).click();
  await expectBook(evidence, 4, 0, 6);
  expect(mutations).toEqual([]);
});

test("uncertain run, export, replay and reset preserve backend evidence", async ({
  page,
}) => {
  await openExperiment(page);
  const reset = page.getByRole("button", {
    name: "Reset to new experiment",
    exact: true,
  });
  if (await reset.isEnabled()) await reset.click();
  await page
    .getByRole("button", { name: "Run experiment", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "All stages complete" }),
  ).toBeDisabled();
  const { evidence } = panels(page);
  await expectBook(evidence, 4, 0, 6);
  const firstId = await page
    .getByLabel("Uncertain experiment record")
    .inputValue();
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Export evidence", exact: true })
    .click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe(firstId + "-evidence.json");
  const bundle = JSON.parse(fs.readFileSync((await download.path())!, "utf8"));
  expect(bundle.verification.valid).toBe(true);
  expect(bundle.verification.negative_control_detected).toBe(true);
  expect(bundle.verification.conformance.evidence.valid).toBe(true);
  expect(bundle.verification.conformance.timeout.valid).toBe(false);
  expect(bundle.view.stage).toBe("cancellations");
  for (const policy of ["evidence", "timeout"] as const) {
    const recorded = bundle.view.policies[policy];
    await expectBook(
      panels(page)[policy],
      recorded.book.consumed,
      recorded.book.reserved,
      recorded.book.available,
    );
    await expect(
      panels(page)[policy].locator("[data-metric=obligation]"),
    ).toHaveText(`${recorded.observer.obligation_units} units`);
  }
  const checkpoint = bundle.frames.find(
    (frame: { stage: string }) => frame.stage === "second_batch",
  );
  expect(checkpoint.policies.evidence.observer.obligation_units).toBe(10);
  expect(checkpoint.policies.timeout.observer.obligation_units).toBe(20);
  expect(JSON.stringify(bundle)).not.toContain(
    fs.readFileSync(".test-token", "utf8"),
  );

  await reset.click();
  await expect(
    page.getByRole("heading", { name: "Ready to begin", exact: true }),
  ).toBeVisible();
  const freshId = await page
    .getByLabel("Uncertain experiment record")
    .inputValue();
  expect(freshId).not.toBe(firstId);
  await expectBook(panels(page).evidence, 0, 0, 10);
  await page.getByLabel("Uncertain experiment record").selectOption(firstId);
  await expectBook(panels(page).evidence, 4, 0, 6);
  await page.reload();
  await expect(
    page.getByRole("heading", {
      name: "A missing receipt does not create capacity",
    }),
  ).toBeVisible();
  await expect(page.getByLabel("Uncertain experiment record")).toHaveValue(
    firstId,
  );
  await expectBook(panels(page).evidence, 4, 0, 6);
  const docs = await page.request.get("/docs/uncertain-execution");
  expect(docs.ok()).toBe(true);
  expect(await docs.text()).toContain("reproduce_uncertain");
});

test("repeated uncertain step clicks commit a stage only once", async ({
  page,
}) => {
  await openExperiment(page, true);
  await page
    .getByRole("button", { name: "Step: Initial admission", exact: true })
    .evaluate((button) => {
      (button as HTMLButtonElement).click();
      (button as HTMLButtonElement).click();
    });
  await expect(
    page.getByRole("heading", { name: "Initial admission", exact: true }),
  ).toBeVisible();
  await expectBook(panels(page).evidence, 0, 10, 0);
  const id = await page.getByLabel("Uncertain experiment record").inputValue();
  const response = await page.request.get("/api/demo/uncertain/" + id);
  const value = await response.json();
  expect(value.completed_stages).toEqual(["admission"]);
  expect(
    value.frames.filter(
      (frame: { stage: string }) => frame.stage === "admission",
    ),
  ).toHaveLength(1);
  await page.getByRole("button", { name: "End demo", exact: true }).click();
});

test("public uncertain experiments remain isolated and fit a phone", async ({
  page,
  browser,
}) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await openExperiment(page, true);
  await page
    .getByRole("button", { name: "Run experiment", exact: true })
    .click();
  await expectBook(panels(page).evidence, 4, 0, 6);
  await expect(
    panels(page).timeout.locator("[data-metric=obligation]"),
  ).toHaveText("14 units");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: evidenceScreenshot("uncertainty-mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Inspect checkpoint replay" }).click();
  await expect(
    page.getByText("Replay · recorded backend frame", { exact: true }),
  ).toBeVisible();
  await expect(
    panels(page).timeout.locator("[data-metric=obligation]"),
  ).toHaveText("20 units");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const id = await page.getByLabel("Uncertain experiment record").inputValue();
  const other = await browser.newContext();
  try {
    const second = await other.newPage();
    await openExperiment(second, true);
    const denied = await second.request.get("/api/demo/uncertain/" + id);
    expect(denied.ok()).toBe(false);
    await expect(
      second.getByRole("button", { name: "Run experiment", exact: true }),
    ).toBeEnabled();
    await second.getByRole("button", { name: "End demo", exact: true }).click();
  } finally {
    await other.close();
  }
  await page.getByRole("button", { name: "End demo", exact: true }).click();
});
