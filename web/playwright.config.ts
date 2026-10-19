import { defineConfig, devices } from "@playwright/test";

// Several worktrees run e2e on this machine: give each its own port with
// E2E_PORT (PW_PORT is accepted as an alias).
const PORT = Number(process.env.E2E_PORT ?? process.env.PW_PORT ?? 4173);
const OUT = `dist-e2e-${PORT}`;

// e2e runs the production build under `vite preview` with every endpoint
// mocked (VITE_MOCK_ALL=1), so no Python server or database is needed.
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}/dashboard/`,
    viewport: { width: 1440, height: 900 },
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `VITE_MOCK_ALL=1 npx vite build --outDir ${OUT} && npx vite preview --outDir ${OUT} --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}/dashboard/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
