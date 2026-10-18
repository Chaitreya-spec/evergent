import { expect, test } from "@playwright/test";
import { register, shot } from "./helpers";

test("plan, approve, watch it run, and inspect the results", async ({ page }) => {
  await register(page);

  // First visit: the composer asks for a project.
  await page.getByRole("textbox", { name: "Project" }).fill("Notes service");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("combobox", { name: "Project" })).toContainText("Notes service");

  await page.getByLabel("What should the team build?").fill("Build a tiny notes API with a database schema and a README");
  await shot(page, "02-composer");
  await page.getByRole("button", { name: "Plan it" }).click();

  // Planning happens live; the plan then waits for approval.
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]+$/);
  const banner = page.getByText(/The plan is ready: 4 tasks/);
  await expect(banner).toBeVisible({ timeout: 30_000 });
  const canvas = page.getByLabel("Workflow graph");
  await expect(canvas.getByText("Build the notes API")).toBeVisible();
  await expect(canvas.getByText("Backend Engineer")).toBeVisible();
  await shot(page, "03-awaiting-approval");

  // Inspect a task before approving it.
  await canvas.getByText("Build the notes API").click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector.getByRole("heading", { name: "Build the notes API" })).toBeVisible();
  await expect(inspector.getByText("Write app.py with GET and POST /notes.")).toBeVisible();
  await expect(inspector.getByRole("button", { name: "Edit" })).toBeVisible();

  await page.getByRole("button", { name: "Approve and run" }).click();
  const summary = page.getByRole("region", { name: "Workflow summary" });
  await expect(summary.getByText("Completed", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(summary.getByText("4/4 tasks")).toBeVisible();
  // The graph stays on screen through every live update.
  await expect(canvas.getByText("Document it")).toBeVisible();
  await expect(canvas.locator(".react-flow__node")).toHaveCount(10);
  await shot(page, "04-completed");

  // The console holds the record of what happened.
  await expect(page.getByRole("log")).toContainText("Workflow completed");
  await page.getByRole("tab", { name: /generated files/i }).click();
  for (const file of ["SPEC.md", "app.py", "schema.sql", "README.md"]) {
    await expect(page.getByRole("option", { name: new RegExp(file.replace(".", "\\.")) })).toBeVisible();
  }
  await page.getByRole("option", { name: /schema\.sql/ }).click();
  await expect(page.getByText("create table notes")).toBeVisible();
  await shot(page, "05-files");

  await page.getByRole("tab", { name: /terminal/i }).click();
  await expect(page.getByText("No interactive terminal")).toBeVisible();

  // Final integration holds the synthesized answer.
  await canvas.getByText("Final integration").click();
  await expect(inspector.getByText(/A tiny notes API: a spec, the API/)).toBeVisible();

  // History lists it with its real cost.
  await page.goto("/workflows");
  await expect(page.getByText("Build a tiny notes API with a database schema and a README")).toBeVisible();
});

test("a duplicate submit creates one workflow, not two", async ({ page }) => {
  await register(page);
  const project = await (await page.request.post("/api/v1/projects", { data: { name: "Idem" } })).json();
  const body = { project_id: project.id, goal: "Build a counter in plain JavaScript", autostart: false };
  const headers = { "Idempotency-Key": "same-click" };
  const first = await page.request.post("/api/v1/workflows", { data: body, headers });
  const second = await page.request.post("/api/v1/workflows", { data: body, headers });
  expect(first.status()).toBe(201);
  expect(second.status()).toBe(200);
  expect((await first.json()).id).toBe((await second.json()).id);
});

test("a cross-site request is refused by the proxy", async ({ page }) => {
  await register(page);
  const response = await page.request.post("/api/v1/projects", {
    data: { name: "Forged" },
    headers: { Origin: "https://evil.example" },
  });
  expect(response.status()).toBe(403);
});
