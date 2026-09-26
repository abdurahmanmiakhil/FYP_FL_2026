import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";
import path from "node:path";

export const PASSWORD = process.env.E2E_PASSWORD ?? "E2e-Demo-Password-2026!";
export const USERS = {
  admin: "admin@gleasonai.demo",
  pathologist: "pathologist.radboud@gleasonai.demo",
  urologist: "urologist.radboud@gleasonai.demo",
} as const;

export const SHOTS = path.resolve(__dirname, "../../docs/screenshots");

export async function login(page: Page, email: string, password = PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Welcome");
}

export async function logout(page: Page) {
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
}

/** WCAG 2.1 AA scan (axe-core - the engine behind Lighthouse's accessibility audit). */
export async function expectAccessible(page: Page, exclude: string[] = []) {
  let builder = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]);
  for (const sel of exclude) builder = builder.exclude(sel);
  const { violations } = await builder.analyze();
  const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const report = serious.flatMap((v) =>
    v.nodes.map(
      (n) => `${v.id}: ${n.target.join(" ")} - ${n.failureSummary?.replace(/\s+/g, " ").slice(0, 200)}`,
    ),
  );
  expect(report).toEqual([]);
}

export async function shot(page: Page, name: string) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: false });
}
