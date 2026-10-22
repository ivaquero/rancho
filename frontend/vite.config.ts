import { defineConfig } from "vite";

// The client talks to the FastAPI service on :8000 during development; in
// production `rancho serve` mounts `dist/` at the same origin, so no proxy is
// needed and `/api` stays relative.
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
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
