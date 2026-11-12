/* eslint-env node */
import assert from "node:assert/strict";
import test from "node:test";
import { checkContractCoverage } from "./check-runtime-management-api-contract-coverage.mjs";

const allowances = [
  {
    path: "futureagi/accounts/views/handoff_exchange.py",
    class: "HandoffRedeemView",
    function: "post",
    reason: "Preserve one-time sign-in code refusal responses until paired post-launch conversion.",
  },
  {
    path: "futureagi/accounts/views/handoff_exchange.py",
    class: "WebSocketTicketView",
    function: "post",
    reason: "Preserve socket-ticket sign-in refusal responses until paired post-launch conversion.",
  },
  {
    path: "futureagi/agentcc/views/mcp_approvals.py",
    class: "MCPApprovalDecisionView",
    function: "post",
    reason: "Convert the MCP approval decision to validated_request after launch.",
  },
];
const entries = allowances.map(({ path, class: className, function: method }) => ({
  path,
  class: className,
  function: method,
  line: 100,
  serializers: ["FixtureSerializer"],
}));

function fixture(management = entries.slice(0, 2), appWide = entries) {
  return {
    summary: {
      runtime_backed_validated_request_decorators: 317,
      direct_swagger_auto_schema_decorators: 138,
      doc_only_input_contract_decorators: management.length,
      broad_request_contract_decorators: 0,
    },
    app_wide_summary: {
      runtime_backed_validated_request_decorators: 458,
      direct_swagger_auto_schema_decorators: 288,
      doc_only_input_contract_decorators: appWide.length,
      broad_request_contract_decorators: 0,
    },
    doc_only_input_contract_decorators: management,
    app_wide_doc_only_input_contract_decorators: appWide,
  };
}

function assertNamedFailure(result, entry) {
  assert.ok(
    result.failures.some((failure) =>
      failure.includes(entry.path) &&
      failure.includes(`${entry.class}.${entry.function}`) &&
      !failure.includes("remove the stale allowance")),
    `Expected a failure naming ${entry.path} ${entry.class}.${entry.function}: ${result.failures.join("; ")}`,
  );
}

test("the three exact named entries pass across management and app-wide scopes", () => {
  assert.deepEqual(checkContractCoverage(fixture(), allowances).failures, []);
});

test("a fourth doc-only decorator fails with its full identity", () => {
  const fourth = { path: "futureagi/accounts/views/other.py", class: "OtherView", function: "post" };
  assertNamedFailure(checkContractCoverage(fixture([...entries.slice(0, 2), fourth], [...entries, fourth]), allowances), fourth);
});

for (const field of ["class", "function", "path"]) {
  test(`changing a named entry's ${field} fails`, () => {
    const renamed = { ...entries[0], [field]: `${entries[0][field]}Changed` };
    const result = checkContractCoverage(fixture([renamed, entries[1]], [renamed, ...entries.slice(1)]), allowances);
    assertNamedFailure(result, renamed);
    assert.ok(result.failures.some((failure) => failure.includes("remove the stale allowance")));
  });
}

test("an unused allowance fails with an instruction to remove it", () => {
  const result = checkContractCoverage(fixture(entries.slice(0, 2), entries.slice(0, 2)), allowances);
  assert.ok(result.failures.some((failure) =>
    failure.includes("remove the stale allowance") &&
    failure.includes(`${entries[2].class}.${entries[2].function}`) &&
    failure.includes(entries[2].path)));
});

test("zero doc-only decorators and an empty allowance pass", () => {
  assert.deepEqual(checkContractCoverage(fixture([], []), []).failures, []);
});

test("line and serializer changes do not change identity", () => {
  const moved = entries.map((entry) => ({ ...entry, line: 900, serializers: ["RenamedSerializer"] }));
  assert.deepEqual(checkContractCoverage(fixture(moved.slice(0, 2), moved), allowances).failures, []);
});

test("allowances are explicit, not implicitly enabled for callers", () => {
  assertNamedFailure(checkContractCoverage(fixture()), entries[0]);
});

test("allowance paths are literal, not glob patterns", () => {
  const patterns = allowances.map((entry) => ({ ...entry, path: "futureagi/**/views/*.py" }));
  assertNamedFailure(checkContractCoverage(fixture(), patterns), entries[0]);
});

test("an unexpected management-only entry is not hidden by the app-wide list", () => {
  const extra = { path: "futureagi/accounts/views/extra.py", class: "ExtraView", function: "post" };
  assertNamedFailure(checkContractCoverage(fixture([...entries.slice(0, 2), extra]), allowances), extra);
});

test("duplicate decorators within a scope fail even with a named allowance", () => {
  const result = checkContractCoverage(fixture([entries[0], entries[0], entries[1]]), allowances);
  assert.ok(result.failures.some((failure) => failure.includes("duplicate") && failure.includes("HandoffRedeemView.post")));
});

test("summary counts cannot hide decorators missing from the identity list", () => {
  const report = fixture();
  report.app_wide_summary.doc_only_input_contract_decorators += 1;
  const result = checkContractCoverage(report, allowances);
  assert.ok(result.failures.some((failure) => failure.includes("summary") && failure.includes("identity list")));
});

test("missing identity lists cannot satisfy positive summary counts", () => {
  const report = fixture();
  delete report.doc_only_input_contract_decorators;
  assert.ok(checkContractCoverage(report, allowances).failures.some((failure) => failure.includes("identity list")));
});

test("the six non-doc-only checks retain their thresholds and messages", () => {
  const report = fixture();
  report.summary.runtime_backed_validated_request_decorators = 316;
  report.summary.direct_swagger_auto_schema_decorators = 139;
  report.summary.broad_request_contract_decorators = 1;
  report.app_wide_summary.runtime_backed_validated_request_decorators = 457;
  report.app_wide_summary.direct_swagger_auto_schema_decorators = 289;
  report.app_wide_summary.broad_request_contract_decorators = 1;
  const result = checkContractCoverage(report, allowances);
  assert.deepEqual(result.failures, [
    "runtime-backed validated_request decorators: expected >= 317, found 316",
    "direct swagger_auto_schema decorators: expected <= 138, found 139",
    "broad request contract decorators: expected <= 0, found 1",
    "app-wide runtime-backed validated_request decorators: expected >= 458, found 457",
    "app-wide direct swagger_auto_schema decorators: expected <= 288, found 289",
    "app-wide broad request contract decorators: expected <= 0, found 1",
  ]);
  assert.deepEqual(result.output.split("\n").filter((line) => !line.includes("doc-only")), [
    "Runtime Management API contract coverage:",
    "  runtime-backed validated_request decorators: 316/317 minimum",
    "  direct swagger_auto_schema decorators: 139/138 maximum",
    "  broad request contract decorators: 1/0 maximum",
    "Runtime Product API contract coverage:",
    "  runtime-backed validated_request decorators: 457/458 minimum",
    "  direct swagger_auto_schema decorators: 289/288 maximum",
    "  broad request contract decorators: 1/0 maximum",
  ]);
});

test("checking a report does not mutate its inputs", () => {
  const report = fixture();
  const before = JSON.stringify({ report, allowances });
  checkContractCoverage(report, allowances);
  assert.equal(JSON.stringify({ report, allowances }), before);
});
