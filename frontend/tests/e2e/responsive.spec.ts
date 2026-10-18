import { expect, test } from "@playwright/test";
import { register, shot } from "./helpers";

test("the workspace works at phone width", async ({ page }) => {
  await register(page);
  await shot(page, "09-mobile-home");
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(page.viewportSize()!.width);

  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "Usage & cost" }).click();
  await expect(page.getByRole("heading", { name: "Usage & cost" })).toBeVisible();
});
