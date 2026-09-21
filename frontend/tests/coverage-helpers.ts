import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

export async function execute(page: Page, schedule: string) {
  const result = page.waitForResponse((response) => response.url().endsWith(`/coverage/${schedule}/run`) && response.request().method() === "POST");
  await page.getByRole("button", { name: `Run ${schedule} schedule`, exact: true }).click();
  const response = await result;
  expect(response.ok(), await response.text()).toBeTruthy();
  const observed = await response.json();
  await expect(page.getByRole("region", { name: "Observed run replay" })).toBeVisible();
  return observed;
}

export function screenshotPath(name: string) {
  const directory = process.env.SAAC_EVIDENCE_DIR;
  if (directory) fs.mkdirSync(directory, { recursive: true });
  return directory ? path.join(directory, name) : test.info().outputPath(name);
}

export async function inspectPublicCoverage(page: Page) {
  // Reuse the authenticated visitor already created by public-demo.spec.ts.
  // The full suite remains within the production 10-starts/IP/10-minute cap.
  await page.getByRole("button", { name: "Coverage schedules C8–C10", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agents choose actions. Institutions set the limits." })).toBeVisible();
  await expect(page.locator(".coverage-feedback")).not.toHaveText("Loading coverage schedules");
  await expect(page.getByRole("region", { name: "Agentic RISC architecture" })).toContainText("Actor environment");
  await expect(page.getByRole("region", { name: "Agentic RISC architecture" })).toContainText("Institutional authority book");
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
}
