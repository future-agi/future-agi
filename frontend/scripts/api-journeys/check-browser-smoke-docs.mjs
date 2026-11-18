/* eslint-disable no-console */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const BROWSER_SMOKE_REF_PATTERN =
  /\b[\w-]+-smoke(?:\.spec)?\.(?:mjs|js)\b/g;

const DEFAULT_BROWSER_ROOT = resolveDefaultBrowserRoot();
const DEFAULT_DOCS_ROOT = resolveDefaultDocsRoot();

if (isCliEntryPoint()) {
  runCli().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

async function runCli() {
  const args = parseArgs(process.argv.slice(2));
  const summary = await checkBrowserSmokeDocs({
    browserRoot: args.browserRoot,
    docsRoot: args.docsRoot,
    strictUndocumented: args.strictUndocumented,
  });

  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }

  console.log(JSON.stringify(summary, null, 2));
  if (summary.status !== "passed") process.exitCode = 1;
}

export async function checkBrowserSmokeDocs({
  browserRoot = DEFAULT_BROWSER_ROOT,
  docsRoot = DEFAULT_DOCS_ROOT,
  strictUndocumented = false,
} = {}) {
  const browserFiles = await collectBrowserSmokeFiles(browserRoot);
  const docs = await collectDocSmokeRefs(docsRoot);
  const browserFileSet = new Set(browserFiles);
  const missing_files = [...docs.refs].filter(
    (file) => !browserFileSet.has(file),
  );
  const undocumented_smokes = browserFiles.filter(
    (file) => !docs.refs.has(file),
  );

  return {
    status:
      missing_files.length || (strictUndocumented && undocumented_smokes.length)
        ? "failed"
        : "passed",
    browser_root: browserRoot,
    docs_root: docsRoot,
    docs_files_scanned: docs.files.length,
    browser_smoke_count: browserFiles.length,
    docs_ref_count: docs.refs.size,
    missing_files,
    undocumented_smokes,
    strict_undocumented: strictUndocumented,
    docs_by_smoke: docs.bySmoke,
  };
}

export async function collectBrowserSmokeFiles(browserRoot) {
  const entries = await fs.readdir(browserRoot, { withFileTypes: true });
  return entries
    .filter(
      (entry) =>
        entry.isFile() && /^.+-smoke(?:\.spec)?\.(?:mjs|js)$/i.test(entry.name),
    )
    .map((entry) => entry.name)
    .sort();
}

export async function collectDocSmokeRefs(docsRoot) {
  const entries = await fs.readdir(docsRoot, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && /\.(csv|md)$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  const refs = new Set();
  const bySmoke = {};

  for (const file of files) {
    const text = await fs.readFile(path.join(docsRoot, file), "utf8");
    for (const smoke of extractBrowserSmokeRefsFromText(text)) {
      refs.add(smoke);
      bySmoke[smoke] ||= [];
      bySmoke[smoke].push(file);
    }
  }

  for (const smoke of Object.keys(bySmoke)) {
    bySmoke[smoke] = [...new Set(bySmoke[smoke])].sort();
  }

  return { files, refs, bySmoke };
}

export function extractBrowserSmokeRefsFromText(text) {
  return [
    ...new Set(String(text || "").match(BROWSER_SMOKE_REF_PATTERN) || []),
  ].sort();
}

function parseArgs(argv) {
  const args = {
    browserRoot: DEFAULT_BROWSER_ROOT,
    docsRoot: DEFAULT_DOCS_ROOT,
    jsonPath: "",
    strictUndocumented: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--browser-root") {
      args.browserRoot = path.resolve(argv[++index] || "");
    } else if (arg === "--docs-root") {
      args.docsRoot = path.resolve(argv[++index] || "");
    } else if (arg === "--json") {
      args.jsonPath = argv[++index] || "";
    } else if (arg === "--strict-undocumented") {
      args.strictUndocumented = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}

function resolveDefaultBrowserRoot() {
  if (import.meta.url.startsWith("file:")) {
    return path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "browser",
    );
  }

  return path.resolve(process.cwd(), "scripts/api-journeys/browser");
}

function resolveDefaultDocsRoot() {
  if (import.meta.url.startsWith("file:")) {
    return path.resolve(
      path.dirname(fileURLToPath(import.meta.url)),
      "../../../../internal-docs/api-ui-e2e-coverage",
    );
  }

  return path.resolve(process.cwd(), "../../internal-docs/api-ui-e2e-coverage");
}

function isCliEntryPoint() {
  if (!import.meta.url.startsWith("file:")) return false;
  return fileURLToPath(import.meta.url) === process.argv[1];
}
