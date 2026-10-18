import { expect, test } from "@playwright/test";
import { register, shot } from "./helpers";

test("every workspace page loads real data", async ({ page }) => {
  await register(page);

  await page.goto("/models");
  await expect(page.getByRole("heading", { name: "Models & providers" })).toBeVisible();
  await expect(page.getByText("Anthropic").first()).toBeVisible();
  await expect(page.getByText("Configured, not verified").first()).toBeVisible();
  await shot(page, "06-models");

  await page.goto("/agents");
  await expect(page.getByText("Integration Engineer")).toBeVisible();
  await expect(page.getByText("Only administrators can change agents.")).toBeVisible();
  await shot(page, "07-agents");

  await page.goto("/usage");
  await expect(page.getByText("Estimated cost")).toBeVisible();
  await shot(page, "08-usage");

  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).first().click();
  await page.getByLabel("Name").fill("Created in e2e");
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByText("Created in e2e").first()).toBeVisible();

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Security log" })).toBeVisible();
  await expect(page.getByText("auth.register")).toBeVisible();

  await page.goto("/files");
  await expect(page.getByRole("heading", { name: "Files & artifacts" })).toBeVisible();
});

test("keyboard: N starts a new task from anywhere", async ({ page }) => {
  await register(page);
  await page.goto("/usage");
  await page.keyboard.press("n");
  await expect(page).toHaveURL(/\/\?focus=1$/);
  await expect(page.getByLabel("What should the team build?")).toBeFocused();
});
