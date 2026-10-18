import { expect, type Page } from "@playwright/test";

export const PASSWORD = "a-long-enough-password";

export function uniqueEmail(prefix = "e2e"): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}@example.com`;
}

/** Create an account through the UI and land in the workspace. */
export async function register(page: Page, email = uniqueEmail()): Promise<string> {
  await page.goto("/signin?mode=register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: /what are we building/i })).toBeVisible();
  return email;
}

export async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_SCREENSHOTS;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png`, fullPage: false });
}
