import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests drive a real browser against the real frontend and the
 * real backend. Only the AI provider is scripted (backend/tests/e2e/server.py),
 * so the suite needs no API key and is deterministic.
 */
const PYTHON = process.env.E2E_PYTHON ?? "../backend/.venv/bin/python";
const BACKEND_PORT = 8100;
const FRONTEND_PORT = 3100;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // The locally installed Chrome; CI installs Playwright's own Chromium.
    ...(process.env.CI ? {} : { channel: "chrome" }),
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } }, testIgnore: /responsive\.spec\.ts/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /responsive\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `${PYTHON} -m tests.e2e.server`,
      cwd: "../backend",
      url: `http://127.0.0.1:${BACKEND_PORT}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { E2E_PORT: String(BACKEND_PORT) },
    },
    {
      command: `npx next build && npx next start --hostname 127.0.0.1 --port ${FRONTEND_PORT}`,
      url: `http://127.0.0.1:${FRONTEND_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 240_000,
      env: { EVERGENT_API_URL: `http://127.0.0.1:${BACKEND_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
