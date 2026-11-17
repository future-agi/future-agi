import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { scanDist } from "./prototype-leak-scan.mjs";

import { MODULE_MANIFEST, forbiddenModule } from "./public-bundle-plugin.mjs";
export {
  MODULE_MANIFEST,
  forbiddenModule,
  publicBundleIsolationPlugin,
} from "./public-bundle-plugin.mjs";

export function checkPublicBundle(dir) {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(dir, MODULE_MANIFEST), "utf8"),
  );
  if (
    manifest.version !== 1 ||
    !Array.isArray(manifest.chunks) ||
    !manifest.chunks.length
  ) {
    throw new Error("Missing build module provenance");
  }
  const forbidden = [];
  for (const chunk of manifest.chunks) {
    if (
      !Array.isArray(chunk.modules) ||
      !fs.statSync(path.join(dir, chunk.file)).isFile()
    ) {
      throw new Error("Invalid build module provenance");
    }
    forbidden.push(...chunk.modules.filter(forbiddenModule));
  }
  const scan = scanDist(dir);
  if (forbidden.length || scan.present.length || scan.hits.length) {
    throw new Error(JSON.stringify({ forbidden, ...scan }));
  }
  return { chunks: manifest.chunks.length, ...scan };
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    console.log(
      JSON.stringify(
        checkPublicBundle(path.resolve(process.argv[2] || "dist")),
      ),
    );
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
