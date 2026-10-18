import { expect, request as playwrightRequest, test } from "@playwright/test";
import { register } from "./helpers";

test("a personal API key works from a script and stops when revoked", async ({ page, baseURL }) => {
  await register(page);
  await page.goto("/settings");
  await page.getByLabel("Key name").fill("CI pipeline");
  await page.getByRole("button", { name: "Create key" }).click();
  const key = (await page.getByTestId("new-api-key").textContent())?.trim() ?? "";
  expect(key).toMatch(/^evg_[0-9a-f]{8}_/);

  // A client with no cookies at all, as a script would be.
  const script = await playwrightRequest.newContext({ baseURL });
  const auth = { Authorization: `Bearer ${key}` };
  expect((await script.get("/api/v1/auth/me", { headers: auth })).status()).toBe(200);
  const made = await script.post("/api/v1/projects", { headers: auth, data: { name: "Made by a script" } });
  expect(made.status()).toBe(201);
  // A key cannot mint more keys.
  expect((await script.post("/api/v1/api-keys", { headers: auth, data: { name: "more" } })).status()).toBe(403);

  await page.reload();
  await expect(page.getByText("never used").or(page.getByText(/last used/))).toBeVisible();
  await page.getByRole("button", { name: "Revoke" }).click();
  await page.getByRole("button", { name: "Revoke key" }).click();
  await expect(page.getByText(/Revoked/)).toBeVisible();
  expect((await script.get("/api/v1/auth/me", { headers: auth })).status()).toBe(401);
  await script.dispose();
});
