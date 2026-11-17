import fs from "node:fs";
import path from "node:path";

export const MODULE_MANIFEST = ".public-bundle-modules.json";

// Match dependency origins, not minified names or incidental string markers.
export function forbiddenModule(id) {
  const normalized = String(id).replaceAll("\\", "/");
  return (
    /(?:^|\/)src\/(?:_mock\/prototype|test-fixtures)(?:\/|$)/.test(
      normalized,
    ) || /(?:^|\/)node_modules\/(?:msw|@mswjs\/[^/]+)(?:\/|$)/.test(normalized)
  );
}

// Keep the build plugin independent of CLI entry points: Vite bundles its
// config and injects import.meta paths before loading it.
export function publicBundleIsolationPlugin() {
  let root;
  const chunksOf = (bundle) =>
    Object.values(bundle)
      .filter((entry) => entry.type === "chunk")
      .map((entry) => ({
        file: entry.fileName,
        modules: Object.keys(entry.modules).map((id) =>
          path.relative(root, id.replace(/^\0/, "")).replaceAll("\\", "/"),
        ),
      }));
  return {
    name: "public-bundle-isolation",
    apply: "build",
    configResolved(config) {
      root = config.root;
    },
    generateBundle(_options, bundle) {
      const forbidden = chunksOf(bundle).flatMap(({ modules }) =>
        modules.filter(forbiddenModule),
      );
      if (forbidden.length)
        this.error(
          `Mock dependencies in public bundle: ${forbidden.join(", ")}`,
        );
    },
    writeBundle(options, bundle = {}) {
      if (options.dir) {
        // Vite copies public files before writeBundle, regardless of imports.
        fs.rmSync(path.join(options.dir, "mockServiceWorker.js"), {
          force: true,
        });
        // Capture final filenames after output hashing and other generateBundle
        // hooks. Earlier names can differ from the files actually written.
        fs.writeFileSync(
          path.join(options.dir, MODULE_MANIFEST),
          JSON.stringify({ version: 1, chunks: chunksOf(bundle) }),
        );
      }
    },
  };
}
