import { expect, test } from "@playwright/test";
import { PASSWORD, register, shot } from "./helpers";

test("sign-in protects the workspace and returns you where you were going", async ({ page }) => {
  await page.goto("/usage");
  await expect(page).toHaveURL(/\/signin\?next=%2Fusage/);
  await shot(page, "01-signin");

  const email = await register(page);

  // Signing out ends the session on the server, not just in this browser.
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/signin/);
  const me = await page.request.get("/api/v1/auth/me");
  expect(me.status()).toBe(401);

  await page.goto("/usage");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/usage$/);
  await expect(page.getByRole("heading", { name: "Usage & cost" })).toBeVisible();
});

test("a wrong password is refused with a clear message", async ({ page }) => {
  const email = await register(page);
  await page.context().clearCookies();
  await page.goto("/signin");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/signin/);
});

test("tokens never reach page JavaScript", async ({ page }) => {
  await register(page);
  const visible = await page.evaluate(() => document.cookie);
  expect(visible).not.toContain("evg_");
  const cookies = await page.context().cookies();
  for (const name of ["evg_at", "evg_rt"]) {
    const cookie = cookies.find((c) => c.name === name);
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.sameSite).toBe("Lax");
  }
});
