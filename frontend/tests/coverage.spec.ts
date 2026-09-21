import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

test.setTimeout(120000);

async function openCoverage(page: Page, visitor = false) {
  await page.goto(visitor ? "/" : "/#token=" + fs.readFileSync(".test-token", "utf8"));
  if (visitor) await page.getByRole("button", { name: "Start demo", exact: true }).click();
  await page.getByRole("button", { name: "Coverage schedules C8–C10", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agents choose actions. Institutions set the limits." })).toBeVisible();
  await expect(page.locator(".coverage-feedback")).not.toHaveText("Loading coverage schedules");
  await expect(page.getByRole("region", { name: "Agentic RISC architecture" })).toContainText("Actor environment");
  await expect(page.getByRole("region", { name: "Agentic RISC architecture" })).toContainText("Institutional authority book");
}

async function execute(page: Page, schedule: string) {
  const result = page.waitForResponse((response) => response.url().endsWith(`/coverage/${schedule}/run`) && response.request().method() === "POST");
  await page.getByRole("button", { name: `Run ${schedule} schedule`, exact: true }).click();
  const response = await result;
  expect(response.ok(), await response.text()).toBeTruthy();
  const observed = await response.json();
  await expect(page.getByRole("region", { name: "Observed run replay" })).toBeVisible();
  return observed;
}

function screenshotPath(name: string) {
  const directory = process.env.SAAC_EVIDENCE_DIR;
  if (directory) fs.mkdirSync(directory, { recursive: true });
  return directory ? path.join(directory, name) : test.info().outputPath(name);
}

test("C9 observed dashboard separates safe accounting, unsafe conformance and detected breach", async ({ page }) => {
  await openCoverage(page);
  await page.getByRole("button", { name: /C9 Revoking a parent/ }).click();
  await expect(page.locator(".coverage-experiment")).toContainText("implemented/unpinned");
  await expect(page.locator(".coverage-experiment")).toContainText("No observed run selected");
  await expect(page.getByRole("region", { name: "Observed accounting" })).toHaveCount(0);
  const observed = await execute(page, "C9");
  expect(observed.verification.valid, JSON.stringify(observed.verification)).toBeTruthy();
  const safe = page.getByRole("article", { name: "Evidence-preserving Runtime", exact: true });
  const unsafe = page.getByRole("article", { name: "Deliberately unsafe control", exact: true });
  await expect(safe).toContainText("Ordinary conformance: pass");
  await expect(unsafe).toContainText("Ordinary conformance: fail");
  await expect(page.getByRole("region", { name: "Run verification" })).toContainText("Intended breach detected");
  await page.getByRole("button", { name: "Step replay", exact: true }).click();
  await expect(page.getByRole("region", { name: "Observed run replay" })).toContainText("Checkpoint 2");
  await expect(unsafe.locator(".coverage-predicates .coverage-predicate").nth(0)).toContainText("Pass");
  await expect(unsafe.locator(".coverage-predicates .coverage-predicate").nth(1)).toContainText("Fail");
  await page.getByRole("button", { name: "Final checkpoint", exact: true }).click();
  for (const [name, panel] of [["safe", safe], ["unsafe", unsafe]] as const) {
    const final = observed.branches[name].checkpoints.at(-1);
    for (const metric of ["L", "U", "Q"])
      await expect(panel.locator(`[data-metric="${metric}"] dd`)).toHaveText(String(final[metric] / observed.units.scale));
    await expect(panel.locator(".coverage-obligation")).toContainText(`E = ${final.E / observed.units.scale}`);
  }
  await expect(unsafe.locator(".coverage-obligation")).toContainText("Coverage gap E − U − Q: 2");
  await page.screenshot({ path: screenshotPath("coverage-c9-final.png"), fullPage: true });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export observed evidence", exact: true }).click();
  const file = await download;
  const evidence = JSON.parse(fs.readFileSync((await file.path())!, "utf8"));
  expect(evidence.verification.negative_control_detected).toBeTruthy();
  expect(evidence.branches.unsafe.ordinary_conformance).toBeFalsy();
});

test("C10 shows promises, local green books and a red shared allocation from observed state", async ({ page }) => {
  await openCoverage(page);
  await page.getByRole("button", { name: /C10 Two books/ }).click();
  const observed = await execute(page, "C10");
  expect(observed.verification.valid, JSON.stringify(observed.verification)).toBeTruthy();
  const unsafe = page.getByRole("article", { name: "Deliberately unsafe control", exact: true });
  await expect(unsafe.locator(".coverage-obligation")).toContainText("2 outstanding promises");
  await expect(unsafe.locator(".coverage-local-books tbody tr")).toHaveCount(2);
  for (const row of await unsafe.locator(".coverage-local-books tbody tr").all())
    await expect(row.locator("td").last()).toHaveText("Pass");
  await expect(unsafe.locator(".coverage-predicates .coverage-predicate").last()).toContainText("Fail");
  await expect(unsafe.locator('[data-metric="L − U − Q"] dd')).toHaveText("-10");
  await page.getByRole("button", { name: "Step replay", exact: true }).click();
  await expect(unsafe.locator(".coverage-obligation")).toContainText("0 outstanding promises");
  await expect(unsafe.locator(".coverage-obligation")).toContainText("E = 20");
  await expect(page.getByRole("article", { name: "Common covering reservation" }).locator('[data-metric="covering"]')).toContainText("Covering allocation held: 10");
  await expect(page.getByRole("article", { name: "Conserved split of rights" }).locator('[data-metric="covering"]')).toContainText("assigned rights A: 6 + B: 4");
  await page.screenshot({ path: screenshotPath("coverage-c10-final.png"), fullPage: true });
});

test("C8 local operator observes promises, separate-process restart and preserved retries", async ({ page }) => {
  await openCoverage(page);
  const observed = await execute(page, "C8");
  expect(observed.verification.valid, JSON.stringify(observed.verification)).toBeTruthy();
  const safe = page.getByRole("article", { name: "Evidence-preserving Runtime", exact: true });
  await expect(safe.locator(".coverage-obligation")).toContainText("10 outstanding promises");
  await page.getByRole("button", { name: "Step replay", exact: true }).click();
  await expect(safe.locator(".coverage-obligation")).toContainText("0 outstanding promises");
  await page.getByRole("button", { name: "Step replay", exact: true }).click();
  const retry = observed.branches.safe.retry;
  expect(retry.runtime_pid_after).not.toEqual(retry.runtime_pid_before);
  expect(retry.rail_pid_after).toEqual(retry.rail_pid_before);
  expect(retry.same_execution_id).toBeTruthy();
  await expect(safe.locator('[data-metric="Q"] dd')).toHaveText("10");
  await page.getByRole("button", { name: "Step replay", exact: true }).click();
  await page.getByRole("button", { name: "Step replay", exact: true }).click();
  await page.screenshot({ path: screenshotPath("coverage-c8-second-batch.png"), fullPage: true });
});

test("public coverage supports bounded C9/C10 and labels C8 process execution unsupported", async ({ page }) => {
  await openCoverage(page, true);
  await page.getByRole("button", { name: /C8 Independent rail/ }).click();
  await expect(page.getByRole("button", { name: "Run C8 schedule", exact: true })).toBeDisabled();
  await expect(page.locator(".coverage-support")).toContainText("hosted process execution is unsupported");
  await page.getByRole("button", { name: /C9 Revoking a parent/ }).click();
  const observed = await execute(page, "C9");
  expect(observed.support).toBe("bounded hosted schedule");
  expect(observed.verification.valid, JSON.stringify(observed.verification)).toBeTruthy();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Agents choose actions. Institutions set the limits." })).toBeVisible();
  const dimensions = await page.evaluate(() => ({ viewport: window.innerWidth, content: document.documentElement.scrollWidth }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);
  await page.screenshot({ path: screenshotPath("coverage-c9-visitor-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: /C10 Two books/ }).click();
  const shared = await execute(page, "C10");
  expect(shared.support).toBe("bounded hosted schedule");
  expect(shared.verification.valid, JSON.stringify(shared.verification)).toBeTruthy();
  await page.getByRole("button", { name: "Final checkpoint", exact: true }).click();
  await expect(page.getByRole("article", { name: "Deliberately unsafe control", exact: true })
    .locator(".coverage-predicates .coverage-predicate").last()).toContainText("Fail");
  await page.screenshot({ path: screenshotPath("coverage-c10-visitor-mobile.png"), fullPage: true });
});
