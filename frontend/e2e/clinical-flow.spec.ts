import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { expectAccessible, login, logout, shot, USERS } from "./helpers";

const SLIDE = path.resolve(__dirname, ".tmp/e2e-slide.tiff");
const CODE = `E2E-${Date.now().toString(36).toUpperCase()}`;

test.describe.configure({ mode: "serial" });
let caseUrl = "";

test("login page is accessible and rejects a wrong password", async ({ page }) => {
  await page.goto("/login");
  await expectAccessible(page);
  await shot(page, "01-login");
  await page.getByLabel("Email").fill(USERS.pathologist);
  await page.getByLabel("Password").fill("definitely-wrong-Pass1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Incorrect email or password" })).toBeVisible();
});

test("pathologist: upload -> live progress -> result -> heatmap -> review -> PDF", async ({ page }) => {
  await login(page, USERS.pathologist);
  await expect(
    page.getByText("AI decision support only. Final diagnosis requires a pathologist.").first(),
  ).toBeVisible();
  await expectAccessible(page);
  await shot(page, "02-dashboard");

  // upload
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "New upload" }).click();
  await expect(page.getByRole("heading", { name: "Upload a biopsy slide" })).toBeVisible();
  await page.getByLabel("Patient pseudonym code").fill(CODE);
  await page.getByLabel("Slide file").setInputFiles(SLIDE);
  await expect(page.getByText("e2e-slide.tiff")).toBeVisible();
  await shot(page, "03-upload");
  await page.getByRole("button", { name: "Upload and analyse" }).click();

  // live progress then result (real model on CPU: well under 3 minutes)
  await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  caseUrl = page.url();
  await expect(page.getByRole("heading", { name: CODE })).toBeVisible();
  const progress = page.getByText("AI analysis in progress");
  if (await progress.isVisible().catch(() => false)) await shot(page, "04-progress");
  await expect(page.getByRole("heading", { name: "AI result" })).toBeVisible({ timeout: 180_000 });

  // result card: grade, probabilities, model version, provisional status, disclaimer
  await expect(page.getByText(/^ISUP [0-5]$/).first()).toBeVisible();
  await expect(page.getByRole("meter", { name: /P\(clinically significant cancer\)/ })).toBeVisible();
  await expect(page.getByText("Provisional - not yet reviewed")).toBeVisible();
  await expect(page.getByRole("button", { name: /Model version [0-9a-f]{64}/ })).toBeVisible();
  await page.waitForTimeout(1500); // let OpenSeadragon draw the first tiles
  await expectAccessible(page, [".osd-viewer"]);
  await shot(page, "05-case-result");

  // evidence: clicking a top tile zooms the viewer
  await page.getByRole("button", { name: /^Tile 1: attention rank 1/ }).click();
  await page.waitForTimeout(1200);
  await shot(page, "06-case-top-tile");
  await page.getByRole("switch", { name: /Attention heatmap/ }).click();
  await page.getByRole("switch", { name: /Attention heatmap/ }).click();

  // review: amend requires a grade and a comment
  await page.getByRole("radio", { name: /Amend grade/ }).click();
  await page.getByRole("button", { name: "Submit review" }).click();
  await expect(page.getByText("Choose the corrected ISUP grade")).toBeVisible();
  await expect(page.getByText("A comment is required when amending or rejecting")).toBeVisible();
  await page.getByRole("combobox", { name: /Corrected ISUP grade/ }).click();
  const options = page.getByRole("option");
  await options
    .filter({ hasNot: page.locator("[data-disabled]") })
    .first()
    .click();
  await page.getByLabel(/Comment/).fill("E2E: grade amended after review of the top tiles.");
  await page.getByRole("button", { name: "Submit review" }).click();
  await expect(page.getByText("Review recorded in the audit trail.")).toBeVisible();
  await expect(page.getByText("Reviewed", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /History/ }).click();
  await expect(page.getByText("E2E: grade amended after review of the top tiles.")).toBeVisible();
  await shot(page, "07-case-reviewed");

  // PDF report
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Download PDF report" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^gleasonai-E2E-.*\.pdf$/);
  const pdf = readFileSync((await download.path())!);
  expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
  expect(pdf.length).toBeGreaterThan(20_000);

  // case list shows the reviewed case
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Cases", exact: true })
    .click();
  await page.getByLabel("Patient code").fill(CODE);
  await expect(page.getByRole("link", { name: CODE })).toBeVisible();
  await expect(page.getByText("Amended").first()).toBeVisible();
  await expectAccessible(page);
  await shot(page, "08-cases");
  await logout(page);
});

test("admin: every step of the case is in the audit trail; admin pages work", async ({ page }) => {
  test.skip(!caseUrl, "needs the case from the previous test");
  await login(page, USERS.admin);
  await page.goto(caseUrl);
  await page.getByRole("tab", { name: /Audit trail/ }).click();
  for (const action of [
    "Uploaded slide",
    "AI prediction stored",
    "Viewed case",
    "Recorded review",
    "Exported",
  ]) {
    await expect(page.getByText(action).first()).toBeVisible();
  }
  await shot(page, "09-case-audit");

  await page.goto("/admin/audit");
  await page.getByRole("button", { name: "Verify integrity" }).click();
  await expect(page.getByText(/Hash chain intact/)).toBeVisible();
  await expectAccessible(page);
  await shot(page, "10-admin-audit");

  await page.goto("/admin/users");
  await expect(page.getByText(USERS.pathologist)).toBeVisible();
  await expectAccessible(page);
  await shot(page, "11-admin-users");

  await page.goto("/admin/model");
  await expect(page.getByText("0.9705")).toBeVisible();
  await expect(page.getByText(/Ready - models loaded/)).toBeVisible();
  await expectAccessible(page);
  await shot(page, "12-model-card");

  await page.goto("/about");
  await expect(page.getByRole("heading", { name: "How GleasonAI works" })).toBeVisible();
  await expectAccessible(page);
  await shot(page, "13-about");
});

test("urologist is kept out of admin pages", async ({ page }) => {
  await login(page, USERS.urologist);
  await page.goto("/admin/users");
  await expect(page).toHaveURL(/\/dashboard\?denied=1/);
  await expect(page.getByText("That page is only available to administrators.")).toBeVisible();
});
