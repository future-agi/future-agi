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

// Resolve a package's bin script and run it with the current Node binary.
// The .bin shims cannot be used here: on Windows they are .cmd files, which
// Node refuses to spawn without a shell (the extensionless POSIX shim is not
// executable there at all).
const resolveBin = (pkg, binName = pkg) => {
  const pkgDir = path.join(frontendDir, "node_modules", pkg);
  const pkgJsonPath = path.join(pkgDir, "package.json");
  if (!existsSync(pkgJsonPath)) return null;
  const { bin } = JSON.parse(readFileSync(pkgJsonPath, "utf8"));
  const relative = typeof bin === "string" ? bin : bin?.[binName];
  if (!relative) return null;
  return path.join(pkgDir, relative);
};

const run = (bin, args) => {
  const script = resolveBin(bin);
  if (!script || !existsSync(script)) {
    console.error(
      `Missing ${bin}. Run "yarn --cwd frontend install" before committing.`,
    );
    process.exit(1);
  }
  const result = spawnSync(process.execPath, [script, ...args], {
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
