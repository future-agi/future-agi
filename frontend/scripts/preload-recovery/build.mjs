/* eslint-env node */
// CLOUD ONLY. Build the unchanged app entry graph plus a separate PDF fixture.
// This intentionally bypasses dotenv loading and never enables Sentry uploads.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
const root = fileURLToPath(new URL("../../", import.meta.url));
process.chdir(root); // vite.config.js resolves its aliases from the frontend cwd.
process.env.SENTRY_UPLOAD_SOURCEMAPS = "false";
await build({
  root,
  configFile: path.join(root, "vite.config.js"),
  envDir: false,
  build: { manifest: true, outDir: "preload-dist" },
});
await import("./build-pdf-fixture.mjs");
