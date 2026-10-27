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

const MIN_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS = 317;
const MAX_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS = 137;
const MAX_DOC_ONLY_INPUT_CONTRACT_DECORATORS = 0;
const MAX_BROAD_REQUEST_CONTRACT_DECORATORS = 0;
const MIN_APP_WIDE_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS = 458;
const MAX_APP_WIDE_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS = 288;
const MAX_APP_WIDE_DOC_ONLY_INPUT_CONTRACT_DECORATORS = 0;
const MAX_APP_WIDE_BROAD_REQUEST_CONTRACT_DECORATORS = 0;

const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
const summary = report.summary || {};
const appWideSummary = report.app_wide_summary || {};
const failures = [];

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
assertAtMost(
  "doc-only input contract decorators",
  summary.doc_only_input_contract_decorators || 0,
  MAX_DOC_ONLY_INPUT_CONTRACT_DECORATORS,
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
assertAtMost(
  "app-wide doc-only input contract decorators",
  appWideSummary.doc_only_input_contract_decorators || 0,
  MAX_APP_WIDE_DOC_ONLY_INPUT_CONTRACT_DECORATORS,
);
assertAtMost(
  "app-wide broad request contract decorators",
  appWideSummary.broad_request_contract_decorators || 0,
  MAX_APP_WIDE_BROAD_REQUEST_CONTRACT_DECORATORS,
);

console.log(
  [
    "Runtime Management API contract coverage:",
    `  runtime-backed validated_request decorators: ${summary.runtime_backed_validated_request_decorators}/${MIN_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS} minimum`,
    `  direct swagger_auto_schema decorators: ${summary.direct_swagger_auto_schema_decorators}/${MAX_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS} maximum`,
    `  doc-only input contract decorators: ${summary.doc_only_input_contract_decorators}/${MAX_DOC_ONLY_INPUT_CONTRACT_DECORATORS} maximum`,
    `  broad request contract decorators: ${summary.broad_request_contract_decorators}/${MAX_BROAD_REQUEST_CONTRACT_DECORATORS} maximum`,
    "Runtime Product API contract coverage:",
    `  runtime-backed validated_request decorators: ${appWideSummary.runtime_backed_validated_request_decorators}/${MIN_APP_WIDE_RUNTIME_BACKED_VALIDATED_REQUEST_DECORATORS} minimum`,
    `  direct swagger_auto_schema decorators: ${appWideSummary.direct_swagger_auto_schema_decorators}/${MAX_APP_WIDE_DIRECT_SWAGGER_AUTO_SCHEMA_DECORATORS} maximum`,
    `  doc-only input contract decorators: ${appWideSummary.doc_only_input_contract_decorators}/${MAX_APP_WIDE_DOC_ONLY_INPUT_CONTRACT_DECORATORS} maximum`,
    `  broad request contract decorators: ${appWideSummary.broad_request_contract_decorators}/${MAX_APP_WIDE_BROAD_REQUEST_CONTRACT_DECORATORS} maximum`,
  ].join("\n"),
);

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
