/* eslint-env node */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  analyzeTargets,
  buildReport,
  countSummary,
} from "./generate-runtime-management-api-contract-debt.mjs";

// The committed contract-debt reports must not change when an API PR only
// adds well-typed surface. Totals that did made every two API PRs conflict.

const scriptsDir = path.dirname(fileURLToPath(import.meta.url));
const swaggerPath = path.resolve(
  scriptsDir,
  "../../api_contracts/openapi/swagger.json",
);

function withTempDir(callback) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "contract-debt-test-"));
  try {
    return callback(dir);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function managementReport(dir, swagger) {
  const input = path.join(dir, "swagger.json");
  const output = path.join(dir, "report.json");
  fs.writeFileSync(input, JSON.stringify(swagger));
  execFileSync(
    process.execPath,
    [
      path.join(scriptsDir, "generate-management-api-contract-debt.mjs"),
      `--swagger=${input}`,
      `--out=${output}`,
    ],
    { stdio: "ignore" },
  );
  return fs.readFileSync(output, "utf8");
}

test("management debt report is unchanged by a new well-typed path", () => {
  withTempDir((dir) => {
    const swagger = JSON.parse(fs.readFileSync(swaggerPath, "utf8"));
    const before = managementReport(dir, swagger);
    const [template] = Object.keys(swagger.paths).filter((name) =>
      name.startsWith("/tracer/"),
    );
    swagger.paths["/tracer/contract-debt-probe/"] = swagger.paths[template];
    assert.equal(managementReport(dir, swagger), before);
  });
});

test("management debt report still records new debt", () => {
  withTempDir((dir) => {
    const swagger = JSON.parse(fs.readFileSync(swaggerPath, "utf8"));
    const before = JSON.parse(managementReport(dir, swagger));
    swagger.paths["/tracer/contract-debt-probe/"] = {
      post: {
        operationId: "contract_debt_probe",
        responses: { 200: { description: "ok" } },
      },
    };
    const after = JSON.parse(managementReport(dir, swagger));
    assert.equal(
      after.summary.mutation_endpoints_without_body_schema,
      before.summary.mutation_endpoints_without_body_schema + 1,
    );
  });
});

const VIEW = (count) =>
  [
    "class ProbeView(APIView):",
    ...Array.from({ length: count }, (_, index) => [
      "    @validated_request(request_serializer=ProbeSerializer)",
      `    def post_${index}(self, request):`,
      "        pass",
    ]).flat(),
    "",
  ].join("\n");

test("runtime debt report is unchanged by a new validated decorator", () => {
  withTempDir((dir) => {
    const file = path.join(dir, "views.py");
    fs.writeFileSync(file, VIEW(1));
    const before = buildReport([file], [file]);
    const counted = countSummary(analyzeTargets([file]));
    fs.writeFileSync(file, VIEW(2));
    assert.equal(buildReport([file], [file]), before);
    // The coverage check still sees the total, counted in memory.
    assert.equal(
      countSummary(analyzeTargets([file]))
        .runtime_backed_validated_request_decorators,
      counted.runtime_backed_validated_request_decorators + 1,
    );
  });
});
