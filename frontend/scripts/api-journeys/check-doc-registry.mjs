/* eslint-disable no-console */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { journeys } from "./registry.mjs";

export const JOURNEY_ID_PATTERN =
  /\b(?:[A-Z][A-Z0-9]+-API|PUBLIC-AUTH|PUBLIC-SYSTEM|MCP-OAUTH)-\d+\b/g;

const DEFAULT_DOCS_ROOT = resolveDefaultDocsRoot();

if (isCliEntryPoint()) {
  runCli().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

async function runCli() {
  const args = parseArgs(process.argv.slice(2));
  const summary = await checkJourneyDocsAgainstRegistry({
    docsRoot: args.docsRoot,
    registeredJourneys: journeys,
  });

  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }

  console.log(JSON.stringify(summary, null, 2));
  if (summary.status !== "passed") process.exitCode = 1;
}

export async function checkJourneyDocsAgainstRegistry({
  docsRoot = DEFAULT_DOCS_ROOT,
  registeredJourneys = journeys,
} = {}) {
  const docs = await collectDocJourneyIds(docsRoot);
  const registeredIds = [...new Set(registeredJourneys.map((item) => item.id))]
    .filter(Boolean)
    .sort();
  const docIds = [...docs.ids].sort();
  const missing_in_runner = docIds.filter((id) => !registeredIds.includes(id));
  const missing_in_docs = registeredIds.filter((id) => !docs.ids.has(id));
  const duplicate_registered_ids = findDuplicates(
    registeredJourneys.map((item) => item.id),
  );

  return {
    status:
      missing_in_runner.length ||
      missing_in_docs.length ||
      duplicate_registered_ids.length
        ? "failed"
        : "passed",
    docs_root: docsRoot,
    docs_files_scanned: docs.files.length,
    docs_id_count: docIds.length,
    registered_id_count: registeredIds.length,
    missing_in_runner,
    missing_in_docs,
    duplicate_registered_ids,
    docs_by_id: docs.byId,
  };
}

export async function collectDocJourneyIds(docsRoot) {
  const entries = await fs.readdir(docsRoot, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && /\.(csv|md)$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  const ids = new Set();
  const byId = {};

  for (const file of files) {
    const text = await fs.readFile(path.join(docsRoot, file), "utf8");
    for (const id of extractJourneyIdsFromText(text)) {
      ids.add(id);
      byId[id] ||= [];
      byId[id].push(file);
    }
  }

  for (const id of Object.keys(byId)) {
    byId[id] = [...new Set(byId[id])].sort();
  }

  return { files, ids, byId };
}

export function extractJourneyIdsFromText(text) {
  return [
    ...new Set(String(text || "").match(JOURNEY_ID_PATTERN) || []),
  ].sort();
}

function findDuplicates(values) {
  const seen = new Set();
  const duplicates = new Set();
  for (const value of values) {
    if (!value) continue;
    if (seen.has(value)) duplicates.add(value);
    seen.add(value);
  }
  return [...duplicates].sort();
}

function parseArgs(argv) {
  const args = {
    docsRoot: DEFAULT_DOCS_ROOT,
    jsonPath: "",
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--docs-root") {
      args.docsRoot = path.resolve(argv[++index] || "");
    } else if (arg === "--json") {
      args.jsonPath = argv[++index] || "";
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
}

function isCliEntryPoint() {
  if (!import.meta.url.startsWith("file:")) return false;
  return fileURLToPath(import.meta.url) === process.argv[1];
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
