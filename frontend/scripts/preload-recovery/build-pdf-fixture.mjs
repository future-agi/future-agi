/* eslint-env node */
// CLOUD ONLY. From frontend/: node scripts/preload-recovery/build-pdf-fixture.mjs
// Build a supplementary production fixture. Normal app builds never include it.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import react from "@vitejs/plugin-react-swc";
const root = fileURLToPath(new URL("../../", import.meta.url));
await build({
  root,
  configFile: false,
  envDir: false,
  publicDir: false,
  base: "/preload-pdf/",
  plugins: [react()],
  resolve: { alias: { src: path.join(root, "src") } },
  build: {
    outDir: "preload-pdf-dist",
    manifest: true,
    modulePreload: false,
    target: "esnext",
    rollupOptions: {
      input: path.join(root, "scripts/preload-recovery/pdf-fixture.html"),
      output: {
        manualChunks: {
          // Match the app's helper ownership so the PDF chunk stays lazy.
          vendor: ["react", "react-dom", "\0vite/preload-helper.js"],
          pdf: ["pdfjs-dist"],
        },
      },
    },
  },
});
