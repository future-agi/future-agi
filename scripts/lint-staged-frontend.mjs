#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const rootDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const frontendDir = path.join(rootDir, "frontend");

const codeExtensions = new Set([".js", ".jsx", ".ts", ".tsx", ".mjs", ".mts"]);
const prettierExtensions = new Set([
  ...codeExtensions,
  ".json",
  ".yaml",
  ".yml",
  ".css",
  ".scss",
  ".md",
  ".mdx",
]);

const normalizeFrontendPath = (file) => {
  const absolute = path.isAbsolute(file) ? file : path.join(rootDir, file);
  const relativeToRoot = path.relative(rootDir, absolute);
  if (!relativeToRoot.startsWith(`frontend${path.sep}`)) return null;
  if (!existsSync(absolute)) return null;

  const relativeToFrontend = path.relative(frontendDir, absolute);
  if (relativeToFrontend.startsWith(`src${path.sep}generated${path.sep}`)) {
    return null;
  }
  if (
    relativeToFrontend.startsWith(
      path.join("src", "api", "contracts", "openapi-contract.generated"),
    )
  ) {
    return null;
  }
  if (
    relativeToFrontend.startsWith(
      path.join("src", "api", "contracts", "filter-contract.generated"),
    )
  ) {
    return null;
  }
  if (
    relativeToFrontend.startsWith(
      path.join("src", "api", "contracts", "api-surface.generated"),
    )
  ) {
    return null;
  }

  return relativeToFrontend;
};

const files = process.argv.slice(2).map(normalizeFrontendPath).filter(Boolean);
const uniqueFiles = [...new Set(files)];
const codeFiles = uniqueFiles.filter((file) =>
  codeExtensions.has(path.extname(file)),
);
const prettierFiles = uniqueFiles.filter((file) =>
  prettierExtensions.has(path.extname(file)),
);

// Resolve a frontend tool's real JS entry-point from its package.json "bin"
// field instead of the node_modules/.bin shim. The shim is a POSIX symlink (or a
// .cmd wrapper on Windows); since Node's CVE-2024-27980 hardening, spawnSync
// cannot launch a .cmd without shell:true, so spawning the shim fails on Windows
// with "Task failed to spawn". Running the resolved script with process.execPath
// is cross-platform and needs no shell.
const resolveBin = (bin) => {
  const pkgDir = path.join(frontendDir, "node_modules", bin);
  const pkgJsonPath = path.join(pkgDir, "package.json");
  if (!existsSync(pkgJsonPath)) return null;
  const binField = JSON.parse(readFileSync(pkgJsonPath, "utf8")).bin;
  const entry = typeof binField === "string" ? binField : binField?.[bin];
  return entry ? path.join(pkgDir, entry) : null;
};

const run = (bin, args) => {
  const entry = resolveBin(bin);
  if (!entry || !existsSync(entry)) {
    console.error(
      `Missing ${bin}. Run "yarn --cwd frontend install" before committing.`,
    );
    process.exit(1);
  }
  const result = spawnSync(process.execPath, [entry, ...args], {
    cwd: frontendDir,
    stdio: "inherit",
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
};

if (codeFiles.length > 0) {
  run("eslint", ["--fix", ...codeFiles]);
}

if (prettierFiles.length > 0) {
  run("prettier", ["--write", ...prettierFiles]);
}
