/* eslint-disable no-console */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { parseCsv } from "./check-api-inventory-docs.mjs";

const PRODUCT_FEATURE_ID_PATTERN = /\bPF-\d+\b/gu;
const DEFAULT_DOCS_ROOT = resolveDefaultDocsRoot();

if (isCliEntryPoint()) {
  runCli().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

async function runCli() {
  const args = parseArgs(process.argv.slice(2));
  const summary = await checkCoverageDocLinks({
    docsRoot: args.docsRoot,
  });

  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }

  console.log(JSON.stringify(summary, null, 2));
  if (summary.status !== "passed") process.exitCode = 1;
}

export async function checkCoverageDocLinks({
  docsRoot = DEFAULT_DOCS_ROOT,
} = {}) {
  const featureMapPath = path.join(docsRoot, "06-product-feature-map.csv");
  const [featureMap, docsRefs] = await Promise.all([
    collectProductFeatureRows(featureMapPath),
    collectProductFeatureRefs(docsRoot),
  ]);
  const featureIdSet = new Set(
    featureMap.rows.map((row) => row.feature_id).filter(Boolean),
  );
  const unknownProductFeatureRefs = [...docsRefs.refs]
    .filter((featureId) => !featureIdSet.has(featureId))
    .map((featureId) => ({
      feature_id: featureId,
      files: docsRefs.byFeatureId[featureId] || [],
    }))
    .sort((left, right) => left.feature_id.localeCompare(right.feature_id));
  const failed =
    featureMap.duplicate_product_feature_id_count ||
    featureMap.invalid_product_feature_id_count ||
    featureMap.missing_current_flow_file_count ||
    unknownProductFeatureRefs.length;

  return {
    status: failed ? "failed" : "passed",
    docs_root: docsRoot,
    product_feature_map_path: featureMapPath,
    product_feature_row_count: featureMap.rows.length,
    product_feature_ref_count: docsRefs.refs.size,
    docs_files_scanned: docsRefs.files.length,
    duplicate_product_feature_id_count:
      featureMap.duplicate_product_feature_id_count,
    invalid_product_feature_id_count:
      featureMap.invalid_product_feature_id_count,
    missing_current_flow_file_count: featureMap.missing_current_flow_file_count,
    unknown_product_feature_ref_count: unknownProductFeatureRefs.length,
    duplicate_product_feature_ids: featureMap.duplicate_product_feature_ids,
    invalid_product_feature_ids: featureMap.invalid_product_feature_ids,
    missing_current_flow_files: featureMap.missing_current_flow_files,
    unknown_product_feature_refs: unknownProductFeatureRefs,
    docs_by_product_feature_id: docsRefs.byFeatureId,
  };
}

export async function collectProductFeatureRows(featureMapPath) {
  const text = await fs.readFile(featureMapPath, "utf8");
  const records = parseCsv(text);
  const [header, ...rows] = records;
  const columns = new Map(header.map((name, index) => [name, index]));
  const rowsByFeatureId = new Map();
  const invalidProductFeatureIds = [];
  const missingCurrentFlowFiles = [];
  const docsRoot = path.dirname(featureMapPath);
  const productFeatureRows = [];

  for (const [index, row] of rows.entries()) {
    if (!row.some((value) => String(value || "").trim())) continue;
    const featureId = String(row[columns.get("feature_id")] || "").trim();
    const currentFlowFile = String(
      row[columns.get("current_flow_file")] || "",
    ).trim();
    const productFeatureRow = {
      line: index + 2,
      feature_id: featureId,
      feature: String(row[columns.get("feature")] || "").trim(),
      sub_feature: String(row[columns.get("sub_feature")] || "").trim(),
      current_flow_file: currentFlowFile,
    };
    productFeatureRows.push(productFeatureRow);
    const existing = rowsByFeatureId.get(featureId) || [];
    existing.push(productFeatureRow);
    rowsByFeatureId.set(featureId, existing);

    if (!/^PF-\d+$/u.test(featureId)) {
      invalidProductFeatureIds.push({
        ...productFeatureRow,
        problem: "invalid_product_feature_id",
      });
    }

    for (const flowFileRef of parseCurrentFlowFileRefs(currentFlowFile)) {
      if (!isSafeDocsFileRef(flowFileRef)) {
        missingCurrentFlowFiles.push({
          ...productFeatureRow,
          current_flow_file_ref: flowFileRef,
          problem: "invalid_current_flow_file_ref",
        });
        continue;
      }

      const resolvedPath = path.resolve(docsRoot, flowFileRef);
      const docsRelativePath = path.relative(docsRoot, resolvedPath);
      if (
        docsRelativePath.startsWith("..") ||
        path.isAbsolute(docsRelativePath)
      ) {
        missingCurrentFlowFiles.push({
          ...productFeatureRow,
          current_flow_file_ref: flowFileRef,
          problem: "escapes_docs_root",
        });
        continue;
      }

      if (!(await fileExists(resolvedPath))) {
        missingCurrentFlowFiles.push({
          ...productFeatureRow,
          current_flow_file_ref: flowFileRef,
          problem: "missing_current_flow_file",
        });
      }
    }
  }

  const duplicateProductFeatureIds = [...rowsByFeatureId.entries()]
    .filter(([, featureRows]) => featureRows.length > 1)
    .map(([featureId, featureRows]) => ({
      feature_id: featureId,
      rows: featureRows,
    }))
    .sort((left, right) => left.feature_id.localeCompare(right.feature_id));

  return {
    rows: productFeatureRows,
    duplicate_product_feature_id_count: duplicateProductFeatureIds.length,
    invalid_product_feature_id_count: invalidProductFeatureIds.length,
    missing_current_flow_file_count: missingCurrentFlowFiles.length,
    duplicate_product_feature_ids: duplicateProductFeatureIds,
    invalid_product_feature_ids: invalidProductFeatureIds,
    missing_current_flow_files: missingCurrentFlowFiles,
  };
}

export async function collectProductFeatureRefs(docsRoot) {
  const entries = await fs.readdir(docsRoot, { withFileTypes: true });
  const files = entries
    .filter((entry) => entry.isFile() && /\.(csv|md)$/iu.test(entry.name))
    .map((entry) => entry.name)
    .sort();
  const refs = new Set();
  const byFeatureId = {};

  for (const file of files) {
    const text = await fs.readFile(path.join(docsRoot, file), "utf8");
    for (const featureId of extractProductFeatureIdsFromText(text)) {
      refs.add(featureId);
      byFeatureId[featureId] ||= [];
      byFeatureId[featureId].push(file);
    }
  }

  for (const featureId of Object.keys(byFeatureId)) {
    byFeatureId[featureId] = [...new Set(byFeatureId[featureId])].sort();
  }

  return { files, refs, byFeatureId };
}

export function extractProductFeatureIdsFromText(text) {
  return [
    ...new Set(String(text || "").match(PRODUCT_FEATURE_ID_PATTERN) || []),
  ].sort();
}

function parseCurrentFlowFileRefs(currentFlowFile) {
  return String(currentFlowFile || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
}

function isSafeDocsFileRef(fileRef) {
  return /^[\w.-]+\.csv$/u.test(fileRef);
}

async function fileExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
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
