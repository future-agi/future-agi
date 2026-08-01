/* eslint-disable no-console */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { parseCsv } from "./check-api-inventory-docs.mjs";

const DEFAULT_DOCS_ROOT = resolveDefaultDocsRoot();
const ROW_ID_COLUMNS = [
  "flow_id",
  "defect_id",
  "audit_id",
  "endpoint_name",
  "feature_id",
  "title",
  "summary",
];
const EVIDENCE_COLUMNS = [
  "status",
  "last_tested",
  "evidence",
  "api_evidence",
  "ui_evidence",
  "verification",
  "defects_or_fixes",
  "notes",
];
const LIVE_CLAIM_PATTERNS = [
  ["passed_live", /\bpassed live\b/iu],
  ["live_rerun_passed", /\blive rerun passed\b/iu],
  ["live_api", /\blive (?:local )?api(?:s)?\b/iu],
  ["live_browser", /\blive (?:local )?browser\b/iu],
  ["live_local", /\blive local\b/iu],
  ["real_local_api", /\breal local api(?:s)?\b/iu],
  ["authenticated_local_api", /\bauthenticated local api(?:s)?\b/iu],
  ["passed_against_localhost", /\bpassed against localhost:\d+\b/iu],
  ["live_against_localhost", /\blive against localhost:\d+\b/iu],
  ["against_local_apis", /\bagainst local api(?:s)?\b/iu],
];
const BLOCKED_OR_STATIC_PATTERNS = [
  ["blocked", /\bblocked\b/iu],
  ["static_harness", /\bstatic harness\b/iu],
  ["mocked", /\bmock(?:ed)?\b/iu],
  ["not_fresh_proof", /\bnot fresh proof\b/iu],
  ["not_route_evidence", /\bnot route-level evidence\b/iu],
];
const TMP_ARTIFACT_PATTERN =
  /\/tmp\/[^\s'",;)]+?\.(?:json|log|png|jpg|jpeg|webm|zip|txt)\b/giu;
const JSON_ARG_ARTIFACT_PATTERN =
  /--(?:json|preflight-json|output)\s+(\/tmp\/[^\s'",;)]+)/giu;

if (isCliEntryPoint()) {
  runCli().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

async function runCli() {
  const args = parseArgs(process.argv.slice(2));
  const summary = await checkLiveEvidenceDocs({
    docsRoot: args.docsRoot,
    strictMissingArtifacts: args.strictMissingArtifacts,
  });

  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }

  console.log(JSON.stringify(summary, null, 2));
  if (summary.status !== "passed") process.exitCode = 1;
}

export async function checkLiveEvidenceDocs({
  docsRoot = DEFAULT_DOCS_ROOT,
  strictMissingArtifacts = false,
} = {}) {
  const docs = await collectLiveEvidenceRows(docsRoot);
  const missingArtifactRows = docs.live_claim_rows.filter(
    (row) =>
      row.evidence_artifacts.length === 0 && row.qualifier_matches.length === 0,
  );
  const artifactBackedRows = docs.live_claim_rows.filter(
    (row) => row.evidence_artifacts.length > 0,
  );
  const blockedOrStaticRows = docs.live_claim_rows.filter(
    (row) => row.qualifier_matches.length > 0,
  );

  return {
    status:
      strictMissingArtifacts && missingArtifactRows.length
        ? "failed"
        : "passed",
    docs_root: docsRoot,
    strict_missing_artifacts: strictMissingArtifacts,
    docs_files_scanned: docs.files.length,
    live_claim_row_count: docs.live_claim_rows.length,
    artifact_backed_live_claim_row_count: artifactBackedRows.length,
    blocked_or_static_live_mention_count: blockedOrStaticRows.length,
    missing_live_artifact_row_count: missingArtifactRows.length,
    missing_live_artifact_rows: missingArtifactRows,
    artifact_backed_live_claim_rows: artifactBackedRows,
    blocked_or_static_live_mentions: blockedOrStaticRows,
  };
}

export async function collectLiveEvidenceRows(docsRoot) {
  const entries = await fs.readdir(docsRoot, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && /\.csv$/iu.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  const liveClaimRows = [];

  for (const file of files) {
    const records = parseCsv(
      await fs.readFile(path.join(docsRoot, file), "utf8"),
    );
    const [header = [], ...rows] = records;
    const columns = new Map(
      header.map((name, index) => [String(name || "").trim(), index]),
    );

    for (const [index, row] of rows.entries()) {
      if (!row.some((value) => String(value || "").trim())) continue;
      const fields = collectEvidenceFields({ columns, row });
      const evidenceText = fields.map((field) => field.value).join(" ");
      const claimMatches = matchPatternNames(LIVE_CLAIM_PATTERNS, evidenceText);
      if (!claimMatches.length) continue;

      liveClaimRows.push({
        file,
        line: index + 2,
        row_id: findRowId({ columns, row }),
        status: getCell({ columns, row, column: "status" }),
        claim_matches: claimMatches,
        qualifier_matches: matchPatternNames(
          BLOCKED_OR_STATIC_PATTERNS,
          evidenceText,
        ),
        evidence_artifacts: extractEvidenceArtifactRefs(evidenceText),
        evidence_fields: fields,
        snippet: summarizeEvidenceText(evidenceText),
      });
    }
  }

  return { files, live_claim_rows: liveClaimRows };
}

export function extractEvidenceArtifactRefs(text) {
  const refs = new Set();
  for (const match of String(text || "").matchAll(TMP_ARTIFACT_PATTERN)) {
    refs.add(match[0].replace(/[.`]+$/u, ""));
  }
  for (const match of String(text || "").matchAll(JSON_ARG_ARTIFACT_PATTERN)) {
    refs.add(match[1].replace(/[.`]+$/u, ""));
  }
  return [...refs].sort();
}

function collectEvidenceFields({ columns, row }) {
  return EVIDENCE_COLUMNS.flatMap((column) => {
    if (!columns.has(column)) return [];
    const value = getCell({ columns, row, column });
    if (!value) return [];
    return [{ column, value }];
  });
}

function findRowId({ columns, row }) {
  for (const column of ROW_ID_COLUMNS) {
    const value = getCell({ columns, row, column });
    if (value) return value;
  }
  return "";
}

function getCell({ columns, row, column }) {
  const index = columns.get(column);
  if (index === undefined) return "";
  return String(row[index] || "").trim();
}

function matchPatternNames(patterns, text) {
  return patterns
    .filter(([, pattern]) => pattern.test(text))
    .map(([name]) => name)
    .sort();
}

function summarizeEvidenceText(text) {
  const compact = String(text || "")
    .replace(/\s+/gu, " ")
    .trim();
  if (compact.length <= 260) return compact;
  return `${compact.slice(0, 257)}...`;
}

function parseArgs(argv) {
  const args = {
    docsRoot: DEFAULT_DOCS_ROOT,
    jsonPath: "",
    strictMissingArtifacts: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--docs-root") {
      args.docsRoot = path.resolve(argv[++index] || "");
    } else if (arg === "--json") {
      args.jsonPath = argv[++index] || "";
    } else if (arg === "--strict-missing-artifacts") {
      args.strictMissingArtifacts = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return args;
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
