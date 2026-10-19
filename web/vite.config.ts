/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the Python server at /dashboard (static files, SPA fallback).
export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  server: {
    port: 5173,
    // `npm run dev` against a local Synatyx: API calls go to the Python server.
    proxy: { "/dashboard/api": "http://localhost:8000" },
  },
  preview: { port: 4173, strictPort: true },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 700 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
