#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const rootDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
// Resolve prettier's bin script so it can run with the current Node binary.
// The .bin shim cannot be used here: on Windows it is a .cmd file, which
// Node refuses to spawn without a shell (the extensionless POSIX shim is not
// executable there at all).
const prettierPkgDir = path.join(
  rootDir,
  "frontend",
  "node_modules",
  "prettier",
);
const prettier = (() => {
  const pkgJsonPath = path.join(prettierPkgDir, "package.json");
  if (!existsSync(pkgJsonPath)) return null;
  const { bin } = JSON.parse(readFileSync(pkgJsonPath, "utf8"));
  const relative = typeof bin === "string" ? bin : bin?.prettier;
  return relative ? path.join(prettierPkgDir, relative) : null;
})();

if (!prettier || !existsSync(prettier)) {
  console.error('Missing prettier. Run "yarn --cwd frontend install" first.');
  process.exit(1);
}

const extensions = new Set([
  ".js",
  ".cjs",
  ".mjs",
  ".json",
  ".yaml",
  ".yml",
  ".md",
  ".mdx",
]);
const files = process.argv
  .slice(2)
  .map((file) => (path.isAbsolute(file) ? file : path.join(rootDir, file)))
  .filter((file) => existsSync(file))
  .map((file) => path.relative(rootDir, file))
  .filter((file) => extensions.has(path.extname(file)))
  .filter((file) => !file.startsWith(`frontend${path.sep}`))
  .filter((file) => !file.startsWith(path.join("api_contracts", "openapi")));

const uniqueFiles = [...new Set(files)];
if (uniqueFiles.length === 0) process.exit(0);

const result = spawnSync(
  process.execPath,
  [prettier, "--write", ...uniqueFiles],
  {
    cwd: rootDir,
    stdio: "inherit",
  },
);

process.exit(result.status ?? 1);
