/* eslint-disable no-console */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const OPENAPI_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const SOURCE_FILE_PREFIXES = [
  "api_contracts/",
  "frontend/",
  "futureagi/",
  "scripts/",
];
const FRONTEND_CALL_SITE_REF_PATTERN =
  /\b(?:api_contracts|frontend|futureagi|scripts)\/[^\s;,`]+/gu;
const DEFAULT_PATHS = resolveDefaultPaths();

if (isCliEntryPoint()) {
  runCli().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

async function runCli() {
  const args = parseArgs(process.argv.slice(2));
  const summary = await checkApiInventoryDocs({
    auditDocsPath: args.auditDocsPath,
    extraAllowlistPath: args.extraAllowlistPath,
    inventoryPath: args.inventoryPath,
    repositoryRoot: args.repositoryRoot,
    strictExtra: args.strictExtra,
    swaggerPath: args.swaggerPath,
  });

  if (args.jsonPath) {
    await fs.writeFile(args.jsonPath, `${JSON.stringify(summary, null, 2)}\n`);
  }

  console.log(JSON.stringify(summary, null, 2));
  if (summary.status !== "passed") process.exitCode = 1;
}

export async function checkApiInventoryDocs({
  auditDocsPath = DEFAULT_PATHS.auditDocsPath,
  extraAllowlistPath = DEFAULT_PATHS.extraAllowlistPath,
  inventoryPath = DEFAULT_PATHS.inventoryPath,
  repositoryRoot = DEFAULT_PATHS.repositoryRoot,
  strictExtra = false,
  swaggerPath = DEFAULT_PATHS.swaggerPath,
} = {}) {
  const [openapiOperations, inventoryRows, extraAllowlist, auditDocs] =
    await Promise.all([
      collectOpenApiOperations(swaggerPath),
      collectInventoryRows(inventoryPath),
      collectExtraAllowlist(extraAllowlistPath),
      collectAuditDocs(auditDocsPath),
    ]);
  const auditIds = auditDocs.audit_ids;
  const sourceFileSummary = await checkInventorySourceFiles({
    inventoryRows,
    repositoryRoot,
  });
  const frontendCallSiteSummary = await checkInventoryFrontendCallSites({
    inventoryRows,
    repositoryRoot,
  });
  const inventoryAuditRefSummary = checkInventoryAuditRefs({
    auditIds,
    inventoryRows,
  });
  const openapiKeys = new Set(
    openapiOperations.map((operation) => operation.key),
  );
  const inventoryByKey = groupInventoryRowsByKey(inventoryRows);
  const inventoryKeys = new Set(inventoryByKey.keys());
  const duplicateInventoryOperations = [...inventoryByKey.entries()]
    .filter(([, rows]) => rows.length > 1)
    .map(([key, rows]) => ({
      ...splitOperationKey(key),
      rows: rows.map((row) => ({
        line: row.line,
        endpoint_name: row.endpoint_name,
        status: row.status,
      })),
    }))
    .sort(compareOperation);
  const invalidInventoryRows = inventoryRows
    .filter(
      (row) => !row.method || !OPENAPI_METHODS.has(row.method) || !row.path,
    )
    .map((row) => ({
      line: row.line,
      endpoint_name: row.endpoint_name,
      method: row.method,
      path: row.path,
    }));
  const missingOpenapiOperations = openapiOperations
    .filter((operation) => !inventoryKeys.has(operation.key))
    .map(({ key, ...operation }) => operation)
    .sort(compareOperation);
  const extraInventoryOperations = [...inventoryByKey.entries()]
    .filter(([key]) => !openapiKeys.has(key))
    .map(([key, rows]) => ({
      ...splitOperationKey(key),
      rows: rows.map((row) => ({
        line: row.line,
        endpoint_name: row.endpoint_name,
        status: row.status,
        notes: row.notes,
      })),
    }))
    .sort(compareOperation);
  const extraAllowlistByKey = groupAllowlistByKey(extraAllowlist);
  const allowedExtraKeys = new Set(extraAllowlistByKey.keys());
  const duplicateExtraAllowlistOperations = [...extraAllowlistByKey.entries()]
    .filter(([, rows]) => rows.length > 1)
    .map(([key, rows]) => ({
      ...splitOperationKey(key),
      rows: rows.map((row) => ({
        index: row.index,
        reason: row.reason,
        audit_refs: row.audit_refs,
      })),
    }))
    .sort(compareOperation);
  const invalidExtraAllowlistOperations = extraAllowlist
    .filter(
      (row) =>
        !row.method ||
        !OPENAPI_METHODS.has(row.method) ||
        !row.path ||
        !row.reason ||
        !Array.isArray(row.audit_refs) ||
        row.audit_refs.length === 0,
    )
    .map((row) => ({
      index: row.index,
      method: row.method,
      path: row.path,
      reason: row.reason,
      audit_refs: row.audit_refs,
    }));
  const invalidExtraAllowlistAuditRefs = extraAllowlist
    .filter((row) => row.method && row.path)
    .flatMap((row) => {
      const auditRefs = row.audit_refs.filter((ref) => /^AUD-\d+$/u.test(ref));
      if (!auditRefs.length) {
        return [
          {
            index: row.index,
            method: row.method,
            path: row.path,
            problem: "missing_audit_ref",
            audit_refs: row.audit_refs,
          },
        ];
      }

      return auditRefs
        .filter((ref) => !auditIds.has(ref))
        .map((ref) => ({
          index: row.index,
          method: row.method,
          path: row.path,
          problem: "unknown_audit_ref",
          audit_ref: ref,
          audit_refs: row.audit_refs,
        }));
    });
  const unallowlistedExtraOperations = extraInventoryOperations.filter(
    (operation) =>
      !allowedExtraKeys.has(operationKey(operation.method, operation.path)),
  );
  const currentExtraKeys = new Set(
    extraInventoryOperations.map((operation) =>
      operationKey(operation.method, operation.path),
    ),
  );
  const staleExtraAllowlistOperations = extraAllowlist
    .filter((row) => row.method && row.path && !currentExtraKeys.has(row.key))
    .map((row) => ({
      method: row.method,
      path: row.path,
      reason: row.reason,
      audit_refs: row.audit_refs,
    }))
    .sort(compareOperation);

  const failed =
    invalidInventoryRows.length ||
    duplicateInventoryOperations.length ||
    missingOpenapiOperations.length ||
    unallowlistedExtraOperations.length ||
    staleExtraAllowlistOperations.length ||
    duplicateExtraAllowlistOperations.length ||
    invalidExtraAllowlistOperations.length ||
    invalidExtraAllowlistAuditRefs.length ||
    auditDocs.duplicate_audit_id_count ||
    inventoryAuditRefSummary.invalid_inventory_audit_ref_count ||
    sourceFileSummary.invalid_source_file_ref_count ||
    sourceFileSummary.missing_source_file_ref_count ||
    frontendCallSiteSummary.invalid_frontend_call_site_ref_count ||
    frontendCallSiteSummary.missing_frontend_call_site_ref_count ||
    (strictExtra && extraInventoryOperations.length);

  return {
    status: failed ? "failed" : "passed",
    swagger_path: swaggerPath,
    inventory_path: inventoryPath,
    repository_root: repositoryRoot,
    audit_docs_path: auditDocsPath || null,
    extra_allowlist_path: extraAllowlistPath || null,
    strict_extra: strictExtra,
    openapi_operation_count: openapiOperations.length,
    inventory_row_count: inventoryRows.length,
    inventory_unique_operation_count: inventoryKeys.size,
    missing_openapi_operation_count: missingOpenapiOperations.length,
    extra_inventory_operation_count: extraInventoryOperations.length,
    allowed_extra_inventory_operation_count:
      extraInventoryOperations.length - unallowlistedExtraOperations.length,
    unallowlisted_extra_inventory_operation_count:
      unallowlistedExtraOperations.length,
    stale_extra_allowlist_operation_count: staleExtraAllowlistOperations.length,
    duplicate_extra_allowlist_operation_count:
      duplicateExtraAllowlistOperations.length,
    invalid_extra_allowlist_operation_count:
      invalidExtraAllowlistOperations.length,
    invalid_extra_allowlist_audit_ref_count:
      invalidExtraAllowlistAuditRefs.length,
    duplicate_audit_id_count: auditDocs.duplicate_audit_id_count,
    inventory_audit_ref_count:
      inventoryAuditRefSummary.inventory_audit_ref_count,
    invalid_inventory_audit_ref_count:
      inventoryAuditRefSummary.invalid_inventory_audit_ref_count,
    source_file_ref_count: sourceFileSummary.source_file_ref_count,
    missing_source_file_ref_count:
      sourceFileSummary.missing_source_file_ref_count,
    invalid_source_file_ref_count:
      sourceFileSummary.invalid_source_file_ref_count,
    frontend_call_site_ref_count:
      frontendCallSiteSummary.frontend_call_site_ref_count,
    missing_frontend_call_site_ref_count:
      frontendCallSiteSummary.missing_frontend_call_site_ref_count,
    invalid_frontend_call_site_ref_count:
      frontendCallSiteSummary.invalid_frontend_call_site_ref_count,
    duplicate_inventory_operation_count: duplicateInventoryOperations.length,
    invalid_inventory_row_count: invalidInventoryRows.length,
    missing_openapi_operations: missingOpenapiOperations,
    extra_inventory_operations: extraInventoryOperations,
    unallowlisted_extra_inventory_operations: unallowlistedExtraOperations,
    stale_extra_allowlist_operations: staleExtraAllowlistOperations,
    duplicate_extra_allowlist_operations: duplicateExtraAllowlistOperations,
    invalid_extra_allowlist_operations: invalidExtraAllowlistOperations,
    invalid_extra_allowlist_audit_refs: invalidExtraAllowlistAuditRefs,
    duplicate_audit_ids: auditDocs.duplicate_audit_ids,
    invalid_inventory_audit_refs:
      inventoryAuditRefSummary.invalid_inventory_audit_refs,
    missing_source_file_refs: sourceFileSummary.missing_source_file_refs,
    invalid_source_file_refs: sourceFileSummary.invalid_source_file_refs,
    missing_frontend_call_site_refs:
      frontendCallSiteSummary.missing_frontend_call_site_refs,
    invalid_frontend_call_site_refs:
      frontendCallSiteSummary.invalid_frontend_call_site_refs,
    duplicate_inventory_operations: duplicateInventoryOperations,
    invalid_inventory_rows: invalidInventoryRows,
  };
}

export async function collectOpenApiOperations(swaggerPath) {
  const swagger = JSON.parse(await fs.readFile(swaggerPath, "utf8"));
  const operations = [];

  for (const [operationPath, pathItem] of Object.entries(swagger.paths || {})) {
    for (const method of Object.keys(pathItem || {})) {
      const upperMethod = method.toUpperCase();
      if (!OPENAPI_METHODS.has(upperMethod)) continue;
      operations.push({
        method: upperMethod,
        path: operationPath,
        operation_id: pathItem[method]?.operationId || "",
        key: operationKey(upperMethod, operationPath),
      });
    }
  }

  return operations.sort(compareOperation);
}

export async function collectInventoryRows(inventoryPath) {
  const text = await fs.readFile(inventoryPath, "utf8");
  const records = parseCsv(text);
  const [header, ...rows] = records;
  const columns = new Map(header.map((name, index) => [name, index]));

  return rows
    .filter((row) => row.some((value) => String(value || "").trim()))
    .map((row, index) => {
      const method = String(row[columns.get("method")] || "")
        .trim()
        .toUpperCase();
      const operationPath = String(row[columns.get("path")] || "").trim();
      return {
        line: index + 2,
        feature_area: String(row[columns.get("feature_area")] || "").trim(),
        endpoint_name: String(row[columns.get("endpoint_name")] || "").trim(),
        method,
        path: operationPath,
        status: String(row[columns.get("status")] || "").trim(),
        source_file: String(row[columns.get("source_file")] || "").trim(),
        frontend_call_site: String(
          row[columns.get("frontend_call_site")] || "",
        ).trim(),
        evidence: String(row[columns.get("evidence")] || "").trim(),
        notes: String(row[columns.get("notes")] || "").trim(),
        key: operationKey(method, operationPath),
      };
    });
}

export async function collectExtraAllowlist(extraAllowlistPath) {
  if (!extraAllowlistPath) return [];
  const entries = JSON.parse(await fs.readFile(extraAllowlistPath, "utf8"));
  return entries.map((entry, index) => {
    const method = String(entry.method || "")
      .trim()
      .toUpperCase();
    const operationPath = String(entry.path || "").trim();
    return {
      index,
      method,
      path: operationPath,
      reason: String(entry.reason || "").trim(),
      audit_refs: Array.isArray(entry.audit_refs)
        ? entry.audit_refs.map((value) => String(value).trim()).filter(Boolean)
        : [],
      key: operationKey(method, operationPath),
    };
  });
}

export async function collectAuditIds(auditDocsPath) {
  const auditDocs = await collectAuditDocs(auditDocsPath);
  return auditDocs.audit_ids;
}

export async function collectAuditDocs(auditDocsPath) {
  if (!auditDocsPath) {
    return {
      audit_ids: new Set(),
      duplicate_audit_id_count: 0,
      duplicate_audit_ids: [],
    };
  }
  const text = await fs.readFile(auditDocsPath, "utf8");
  const records = parseCsv(text);
  const [header, ...rows] = records;
  const columns = new Map(header.map((name, index) => [name, index]));
  const auditIdIndex = columns.get("audit_id");
  if (auditIdIndex === undefined) {
    return {
      audit_ids: new Set(),
      duplicate_audit_id_count: 0,
      duplicate_audit_ids: [],
    };
  }

  const rowsByAuditId = new Map();
  for (const [index, row] of rows.entries()) {
    const auditId = String(row[auditIdIndex] || "").trim();
    if (!auditId) continue;
    const existing = rowsByAuditId.get(auditId) || [];
    existing.push({
      line: index + 2,
      audit_id: auditId,
      linked_flow: String(row[columns.get("linked_flow")] || "").trim(),
      status: String(row[columns.get("status")] || "").trim(),
    });
    rowsByAuditId.set(auditId, existing);
  }

  const duplicateAuditIds = [...rowsByAuditId.entries()]
    .filter(([, auditRows]) => auditRows.length > 1)
    .map(([auditId, auditRows]) => ({
      audit_id: auditId,
      rows: auditRows,
    }))
    .sort((left, right) => left.audit_id.localeCompare(right.audit_id));

  return {
    audit_ids: new Set(rowsByAuditId.keys()),
    duplicate_audit_id_count: duplicateAuditIds.length,
    duplicate_audit_ids: duplicateAuditIds,
  };
}

export async function checkInventorySourceFiles({
  inventoryRows,
  repositoryRoot = DEFAULT_PATHS.repositoryRoot,
}) {
  const missingSourceFileRefs = [];
  const invalidSourceFileRefs = [];
  let sourceFileRefCount = 0;

  for (const row of inventoryRows) {
    for (const sourceFileRef of parseSourceFileRefs(row.source_file)) {
      sourceFileRefCount += 1;
      const normalizedRef = sourceFileRef.replaceAll("\\", "/");
      const base = {
        line: row.line,
        endpoint_name: row.endpoint_name,
        source_file: sourceFileRef,
      };

      if (!isRepoRelativeSourceFileRef(normalizedRef)) {
        invalidSourceFileRefs.push({
          ...base,
          problem: "not_repo_relative_source_file",
        });
        continue;
      }

      const resolvedPath = path.resolve(repositoryRoot, normalizedRef);
      const repoRelativePath = path.relative(repositoryRoot, resolvedPath);
      if (
        repoRelativePath.startsWith("..") ||
        path.isAbsolute(repoRelativePath)
      ) {
        invalidSourceFileRefs.push({
          ...base,
          problem: "escapes_repository_root",
        });
        continue;
      }

      if (!(await fileExists(resolvedPath))) {
        missingSourceFileRefs.push(base);
      }
    }
  }

  return {
    source_file_ref_count: sourceFileRefCount,
    missing_source_file_ref_count: missingSourceFileRefs.length,
    invalid_source_file_ref_count: invalidSourceFileRefs.length,
    missing_source_file_refs: missingSourceFileRefs,
    invalid_source_file_refs: invalidSourceFileRefs,
  };
}

export async function checkInventoryFrontendCallSites({
  inventoryRows,
  repositoryRoot = DEFAULT_PATHS.repositoryRoot,
}) {
  const missingFrontendCallSiteRefs = [];
  const invalidFrontendCallSiteRefs = [];
  let frontendCallSiteRefCount = 0;

  for (const row of inventoryRows) {
    for (const frontendCallSiteRef of parseFrontendCallSiteRefs(
      row.frontend_call_site,
    )) {
      frontendCallSiteRefCount += 1;
      const normalizedRef = frontendCallSiteRef.replaceAll("\\", "/");
      const base = {
        line: row.line,
        endpoint_name: row.endpoint_name,
        frontend_call_site: frontendCallSiteRef,
      };

      if (!isRepoRelativeSourceFileRef(normalizedRef)) {
        invalidFrontendCallSiteRefs.push({
          ...base,
          problem: "not_repo_relative_frontend_call_site",
        });
        continue;
      }

      const resolvedPath = path.resolve(repositoryRoot, normalizedRef);
      const repoRelativePath = path.relative(repositoryRoot, resolvedPath);
      if (
        repoRelativePath.startsWith("..") ||
        path.isAbsolute(repoRelativePath)
      ) {
        invalidFrontendCallSiteRefs.push({
          ...base,
          problem: "escapes_repository_root",
        });
        continue;
      }

      if (!(await fileExists(resolvedPath))) {
        missingFrontendCallSiteRefs.push(base);
      }
    }
  }

  return {
    frontend_call_site_ref_count: frontendCallSiteRefCount,
    missing_frontend_call_site_ref_count: missingFrontendCallSiteRefs.length,
    invalid_frontend_call_site_ref_count: invalidFrontendCallSiteRefs.length,
    missing_frontend_call_site_refs: missingFrontendCallSiteRefs,
    invalid_frontend_call_site_refs: invalidFrontendCallSiteRefs,
  };
}

export function checkInventoryAuditRefs({ auditIds, inventoryRows }) {
  const invalidInventoryAuditRefs = [];
  let inventoryAuditRefCount = 0;

  for (const row of inventoryRows) {
    for (const field of ["evidence", "notes"]) {
      for (const auditRef of parseAuditRefs(row[field])) {
        inventoryAuditRefCount += 1;
        if (!auditIds.has(auditRef)) {
          invalidInventoryAuditRefs.push({
            line: row.line,
            endpoint_name: row.endpoint_name,
            field,
            audit_ref: auditRef,
            problem: "unknown_audit_ref",
          });
        }
      }
    }
  }

  return {
    inventory_audit_ref_count: inventoryAuditRefCount,
    invalid_inventory_audit_ref_count: invalidInventoryAuditRefs.length,
    invalid_inventory_audit_refs: invalidInventoryAuditRefs,
  };
}

function parseSourceFileRefs(sourceFile) {
  return String(sourceFile || "")
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseFrontendCallSiteRefs(frontendCallSite) {
  return [
    ...String(frontendCallSite || "").matchAll(FRONTEND_CALL_SITE_REF_PATTERN),
  ]
    .map((match) => match[0].replace(/[)\].:'"]+$/u, ""))
    .filter(Boolean);
}

function parseAuditRefs(text) {
  return [...String(text || "").matchAll(/\bAUD-\d+\b/gu)].map(
    (match) => match[0],
  );
}

function isRepoRelativeSourceFileRef(sourceFileRef) {
  return SOURCE_FILE_PREFIXES.some((prefix) =>
    sourceFileRef.startsWith(prefix),
  );
}

async function fileExists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

export function parseCsv(text) {
  const rows = [];
  let row = [];
  let value = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];

    if (inQuotes) {
      if (char === '"' && next === '"') {
        value += '"';
        index += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        value += char;
      }
      continue;
    }

    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      row.push(value);
      value = "";
    } else if (char === "\n") {
      row.push(value);
      rows.push(row);
      row = [];
      value = "";
    } else if (char !== "\r") {
      value += char;
    }
  }

  if (value || row.length) {
    row.push(value);
    rows.push(row);
  }

  return rows;
}

function groupInventoryRowsByKey(rows) {
  const byKey = new Map();
  for (const row of rows) {
    if (!row.method || !row.path) continue;
    const existing = byKey.get(row.key) || [];
    existing.push(row);
    byKey.set(row.key, existing);
  }
  return byKey;
}

function groupAllowlistByKey(rows) {
  const byKey = new Map();
  for (const row of rows) {
    if (!row.method || !row.path) continue;
    const existing = byKey.get(row.key) || [];
    existing.push(row);
    byKey.set(row.key, existing);
  }
  return byKey;
}

function operationKey(method, operationPath) {
  return `${method} ${operationPath}`;
}

function splitOperationKey(key) {
  const [method, ...pathParts] = key.split(" ");
  return { method, path: pathParts.join(" ") };
}

function compareOperation(left, right) {
  return (
    left.path.localeCompare(right.path) ||
    left.method.localeCompare(right.method)
  );
}

function parseArgs(argv) {
  const args = {
    auditDocsPath: DEFAULT_PATHS.auditDocsPath,
    extraAllowlistPath: DEFAULT_PATHS.extraAllowlistPath,
    inventoryPath: DEFAULT_PATHS.inventoryPath,
    jsonPath: "",
    repositoryRoot: DEFAULT_PATHS.repositoryRoot,
    strictExtra: false,
    swaggerPath: DEFAULT_PATHS.swaggerPath,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--audit-docs") {
      args.auditDocsPath = path.resolve(argv[++index] || "");
    } else if (arg === "--no-audit-docs") {
      args.auditDocsPath = "";
    } else if (arg === "--inventory") {
      args.inventoryPath = path.resolve(argv[++index] || "");
    } else if (arg === "--repo-root") {
      args.repositoryRoot = path.resolve(argv[++index] || "");
    } else if (arg === "--extra-allowlist") {
      args.extraAllowlistPath = path.resolve(argv[++index] || "");
    } else if (arg === "--no-extra-allowlist") {
      args.extraAllowlistPath = "";
    } else if (arg === "--swagger") {
      args.swaggerPath = path.resolve(argv[++index] || "");
    } else if (arg === "--json") {
      args.jsonPath = argv[++index] || "";
    } else if (arg === "--strict-extra") {
      args.strictExtra = true;
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

function resolveDefaultPaths() {
  const scriptDir = path.dirname(fileURLToPath(import.meta.url));
  const frontendRoot = path.resolve(scriptDir, "../..");
  const repositoryRoot = path.resolve(frontendRoot, "..");
  return {
    auditDocsPath: path.resolve(
      scriptDir,
      "../../../../internal-docs/api-ui-e2e-coverage/07-data-integrity-and-dead-code-audit.csv",
    ),
    extraAllowlistPath: path.resolve(
      scriptDir,
      "../../../../internal-docs/api-ui-e2e-coverage/api-inventory-extra-operations.json",
    ),
    inventoryPath: path.resolve(
      scriptDir,
      "../../../../internal-docs/api-ui-e2e-coverage/00-api-inventory.csv",
    ),
    repositoryRoot,
    swaggerPath: path.resolve(
      frontendRoot,
      "../api_contracts/openapi/swagger.json",
    ),
  };
}
