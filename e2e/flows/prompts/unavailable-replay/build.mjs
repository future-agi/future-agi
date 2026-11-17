import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

// Build a separate component-replay artifact, never the application's entry.
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../../..");
const frontend = path.join(repo, "frontend");
const require = createRequire(path.join(frontend, "package.json"));
const { build } = await import(
  pathToFileURL(
    path.join(
      path.dirname(require.resolve("vite/package.json")),
      "dist/node/index.js",
    ),
  )
);
const { default: react } = await import(
  pathToFileURL(
    path.join(
      path.dirname(require.resolve("@vitejs/plugin-react-swc")),
      "index.js",
    ),
  )
);
const outDir = path.join(repo, "out/unavailable-replay-dist");
await build({
  configFile: false,
  envDir: false,
  root: frontend,
  publicDir: false,
  plugins: [react()],
  resolve: {
    alias: [
      { find: "src", replacement: path.join(frontend, "src") },
      ...[
        "react",
        "react/jsx-runtime",
        "react/jsx-dev-runtime",
        "react-dom/client",
        "@tanstack/react-query",
      ].map((name) => ({
        find: new RegExp(`^${name.replaceAll("/", "\\/")}$`),
        replacement: require.resolve(name),
      })),
    ],
  },
  build: {
    outDir,
    emptyOutDir: true,
    rollupOptions: {
      input: path.join(here, "entry.jsx"),
      output: {
        entryFileNames: "replay.js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
await fs.writeFile(
  path.join(outDir, "index.html"),
  '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Isolated component replay</title></head><body><div id="root"></div><script type="module" src="/replay.js"></script></body></html>',
);
