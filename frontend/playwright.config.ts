import { defineConfig, devices } from "@playwright/test";

/**
 * Real-browser E2E: Chromium drives the production frontend build against the
 * real FastAPI backend and a freshly migrated Postgres database.
 *
 *   npm run build && npm run e2e
 *
 * The backend is started with `python -m e2e.serve` (see backend/e2e/serve.py):
 * production code throughout, except that the Greenhouse/Lever adapters read
 * deterministic mock payloads instead of the live job boards.
 *
 * E2E_PYTHON          Python with the backend requirements (default: python3)
 * E2E_CHROMIUM_PATH   optional Chromium executable (otherwise Playwright's own)
 */
const python = process.env.E2E_PYTHON ?? "python3";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    launchOptions: process.env.E2E_CHROMIUM_PATH
      ? { executablePath: process.env.E2E_CHROMIUM_PATH }
      : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `${python} -m e2e.serve`,
      cwd: "../backend",
      url: "http://127.0.0.1:8000/health",
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "ignore",
      env: { BACKEND_CORS_ORIGINS: "http://localhost:3000" },
    },
    {
      command: "npm run start -- --port 3000",
      url: "http://localhost:3000/login",
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
