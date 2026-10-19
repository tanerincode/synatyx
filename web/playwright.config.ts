import { defineConfig, devices } from "@playwright/test";

// e2e runs the production build under `vite preview` with every endpoint
// mocked (VITE_MOCK_ALL=1), so no Python server or database is needed.
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:4173/dashboard/",
    viewport: { width: 1440, height: 900 },
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: "VITE_MOCK_ALL=1 npx vite build --outDir dist-e2e && npx vite preview --outDir dist-e2e",
    url: "http://localhost:4173/dashboard/",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
