import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
// Build straight into the Python package so FastAPI serves it regardless of CWD.
// Dev server proxies API/auth calls to the running FastAPI (pmr-serve on :8011).
export default defineConfig({
    plugins: [react()],
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    build: {
        outDir: path.resolve(__dirname, "../src/playstore_review_service/static/spa"),
        emptyOutDir: true,
    },
    server: {
        port: 5173,
        proxy: {
            "/api": "http://127.0.0.1:8011",
            "/auth": "http://127.0.0.1:8011",
        },
    },
});
