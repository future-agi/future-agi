/* eslint-env node */
// The code-panels walk harness's own Vite config: its root is this folder,
// `src/...` resolves to the frontend's src as in the app, and the test-only
// modules the fixtures import are stood in for (shims/). Started by walk.mjs.
import path from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react-swc";
import { defineConfig } from "vite";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(HERE, "../../../../..");

export default defineConfig({
  root: HERE,
  configFile: false,
  logLevel: "warn",
  clearScreen: false,
  plugins: [react()],
  define: { "import.meta.env.VITE_OMEGA_CONSOLE": JSON.stringify("1") },
  resolve: {
    alias: [
      {
        find: /^vitest$/,
        replacement: path.join(HERE, "shims/test-doubles.js"),
      },
      {
        find: /^@testing-library\/react$/,
        replacement: path.join(HERE, "shims/test-doubles.js"),
      },
      {
        find: /^src\/contexts\/WorkspaceContext$/,
        replacement: path.join(HERE, "shims/test-doubles.js"),
      },
      {
        find: /^src\/sections\/omega\/shared$/,
        replacement: path.join(HERE, "shims/shared.js"),
      },
      { find: /^src\/(.+)/, replacement: `${SRC}/$1` },
    ],
  },
  server: {
    host: "127.0.0.1",
    strictPort: false,
    fs: { allow: [path.resolve(SRC, "..")] },
  },
  optimizeDeps: { entries: [path.join(HERE, "main.jsx")] },
});
