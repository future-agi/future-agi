/* eslint-env node */
/* eslint-disable no-console */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const reportPath = path.join(
  repoRoot,
  "api_contracts",
  "openapi",
  "runtime-management-api-contract-debt.generated.json",
);
const allowancesPath = path.join(__dirname, "runtime-management-api-contract-allowances.json");

const MIN_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS = 317;
const MAX_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS = 138;
const MAX_BROAD_REQUEST_CONTRACT_DECORATORS = 0;
const MIN_APP_WIDE_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS = 458;
const MAX_APP_WIDE_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS = 288;
const MAX_APP_WIDE_BROAD_REQUEST_CONTRACT_DECORATORS = 0;

// Line numbers and serializer names may change without changing the endpoint.
function decoratorKey(entry) {
  return JSON.stringify([entry.path, entry.class, entry.function]);
}

function decoratorName(entry) {
  return `${entry.path} ${entry.class}.${entry.function}`;
}

export function checkContractCoverage(report, allowances = []) {
  const summary = report.summary || {};
  const appWideSummary = report.app_wide_summary || {};
  const failures = [];
  const allowed = new Set(allowances.map(decoratorKey));
  const matchedAllowances = new Set();

  function assertAtMost(name, actual, max) {
    if (actual > max) {
      failures.push(`${name}: expected <= ${max}, found ${actual}`);
    }
  }

  function assertAtLeast(name, actual, min) {
    if (actual < min) {
      failures.push(`${name}: expected >= ${min}, found ${actual}`);
    }
  }

  function assertNamedDocOnly(name, entries, count) {
    if (!Array.isArray(entries)) {
      failures.push(`${name}: expected an identity list`);
      return;
    }
    // Counts only check report integrity; they never authorize a decorator.
    if (count !== entries.length) {
      failures.push(`${name}: summary count ${count} does not match identity list length ${entries.length}`);
    }
    const seen = new Set();
    for (const entry of entries) {
      const key = decoratorKey(entry);
      if (seen.has(key)) {
        failures.push(`${name}: duplicate decorator ${decoratorName(entry)}`);
      }
      seen.add(key);
      if (!allowed.has(key)) {
        failures.push(`${name}: no named allowance for ${decoratorName(entry)}`);
      } else {
        matchedAllowances.add(key);
      }
    }
  }

  assertAtLeast(
    "runtime-backed validated_request decorators",
    summary.runtime_backed_validated_request_decorators || 0,
    MIN_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS,
  );
  assertAtMost(
    "direct swagger_auto_schema decorators",
    summary.direct_swagger_auto_schema_decorators || 0,
    MAX_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS,
  );
  assertNamedDocOnly(
    "doc-only input contract decorators",
    report.doc_only_input_contract_decorators ?? [],
    summary.doc_only_input_contract_decorators || 0,
  );
  assertAtMost(
    "broad request contract decorators",
    summary.broad_request_contract_decorators || 0,
    MAX_BROAD_REQUEST_CONTRACT_DECORATORS,
  );
  assertAtLeast(
    "app-wide runtime-backed validated_request decorators",
    appWideSummary.runtime_backed_validated_request_decorators || 0,
    MIN_APP_WIDE_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS,
  );
  assertAtMost(
    "app-wide direct swagger_auto_schema decorators",
    appWideSummary.direct_swagger_auto_schema_decorators || 0,
    MAX_APP_WIDE_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS,
  );
  assertNamedDocOnly(
    "app-wide doc-only input contract decorators",
    report.app_wide_doc_only_input_contract_decorators ?? [],
    appWideSummary.doc_only_input_contract_decorators || 0,
  );
  assertAtMost(
    "app-wide broad request contract decorators",
    appWideSummary.broad_request_contract_decorators || 0,
    MAX_APP_WIDE_BROAD_REQUEST_CONTRACT_DECORATORS,
  );
  // Management entries also appear app-wide; an allowance is stale only when
  // neither scope contains its exact path + class + function identity.
  for (const allowance of allowances) {
    if (!matchedAllowances.has(decoratorKey(allowance))) {
      failures.push(`doc-only input contract decorators: remove the stale allowance for ${decoratorName(allowance)}`);
    }
  }

  const output = [
    "Runtime Management API contract coverage:",
    `  runtime-backed validated_request decorators: ${summary.runtime_backed_validated_request_decorators}/${MIN_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS} minimum`,
    `  direct swagger_auto_schema decorators: ${summary.direct_swagger_auto_schema_decorators}/${MAX_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS} maximum`,
    `  doc-only input contract decorators: ${summary.doc_only_input_contract_decorators} (exact named allowances only)`,
    `  broad request contract decorators: ${summary.broad_request_contract_decorators}/${MAX_BROAD_REQUEST_CONTRACT_DECORATORS} maximum`,
    "Runtime Product API contract coverage:",
    `  runtime-backed validated_request decorators: ${appWideSummary.runtime_backed_validated_request_decorators}/${MIN_APP_WIDE_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS} minimum`,
    `  direct swagger_auto_schema decorators: ${appWideSummary.direct_swagger_auto_schema_decorators}/${MAX_APP_WIDE_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS} maximum`,
    `  doc-only input contract decorators: ${appWideSummary.doc_only_input_contract_decorators} (exact named allowances only)`,
    `  broad request contract decorators: ${appWideSummary.broad_request_contract_decorators}/${MAX_APP_WIDE_BROAD_REQUEST_CONTRACT_DECORATORS} maximum`,
  ].join("\n");
  return { failures, output };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  const allowances = JSON.parse(fs.readFileSync(allowancesPath, "utf8"));
  const { failures, output } = checkContractCoverage(report, allowances);
  console.log(output);
  if (failures.length) {
    console.error(
      [
        "Runtime Management API contract coverage failed.",
        "Convert doc-only Swagger decorators to validated_request, or lower the baseline after removing debt.",
        ...failures.map((failure) => `  - ${failure}`),
      ].join("\n"),
    );
    process.exit(1);
  }
}
