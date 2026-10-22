import { defineConfig } from "vite";

// The client talks to the FastAPI service on :8000 during development; in
// production `rancho serve` mounts `dist/` at the same origin, so no proxy is
// needed and `/api` stays relative. `rancho dev` injects RANCHO_API so that
// `--api-port` still lands on the right backend.
declare const process: { env: Record<string, string | undefined> };
const apiTarget = process.env.RANCHO_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
});
