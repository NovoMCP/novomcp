/**
 * Vite config for the local Message Inspector dev harness.
 *
 * Run: npm run dev:inspector
 *
 * Serves src/inspector as a normal multi-file app (no singlefile bundling)
 * so we can live-iterate on viewers and providers without deploying.
 */
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: ".",
  server: {
    port: 5174,
    open: "/html/inspector.html",
  },
  build: {
    rollupOptions: {
      input: "html/inspector.html",
    },
    outDir: "dist/inspector",
    emptyOutDir: true,
    sourcemap: "inline",
  },
});
