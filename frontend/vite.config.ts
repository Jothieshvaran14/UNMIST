import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// The API base is resolved at runtime from VITE_API_BASE_URL so the built
// bundle can be pointed at any deployment without a rebuild. In development we
// proxy /api to the local backend, which keeps the browser on one origin and
// therefore avoids depending on the backend's CORS allow-list being configured.
const API_TARGET = process.env.VITE_API_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
