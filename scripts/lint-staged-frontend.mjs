#!/usr/bin/env node
import { existsSync } from "node:fs";
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

const run = (bin, args) => {
  const command = path.join(frontendDir, "node_modules", ".bin", bin);
  if (!existsSync(command)) {
    console.error(
      `Missing ${bin}. Run "yarn --cwd frontend install" before committing.`,
    );
    process.exit(1);
  }
  const result = spawnSync(command, args, {
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
