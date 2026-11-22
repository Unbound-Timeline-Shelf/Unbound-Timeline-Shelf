import { defineConfig, devices } from "@playwright/test";

// Acceptance checks from UX-AUDIT-APP §3.6 against a running host: the
// frontend (Vite dev server or a built image) with its /api backend. Point
// E2E_BASE_URL at it; see README.md for a throwaway lab.
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";

export default defineConfig({
  testDir: ".",
  testMatch: "*.e2e.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [["list"]],
  globalSetup: "./global-setup.ts",
  use: {
    baseURL,
    storageState: "test-results/.auth.json",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "phone",
      use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } },
      testMatch: "navigation-state.e2e.ts",
      grep: /@phone/,
    },
  ],
});
