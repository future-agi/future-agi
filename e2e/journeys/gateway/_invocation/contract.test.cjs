// Authored FIRST. Parse-only locally; execute only in the accepted guarded runner.
const test = require("node:test");
const assert = require("node:assert/strict");
const { catalogue, specCounts } = require("./catalogue.cjs");
const {
  orderedInvocations,
  invocationPlans,
  discoveryInvocations,
  validateBinding,
  validateArguments,
} = require("./consumer.cjs");
const { reconcileInventory, reconcileTerminal } = require("./reconcile.cjs");

const sourceRef = "f59b5541cfdbe747fa26007f2c71501e05924d69";
const input = {
  sourceRef,
  outputRoot: "/landing/f01-contract",
  browsers: ["chromium", "firefox"],
};

test("exact cumulative inventory: eleven packets, 31 specs, 506 titles; no accepted product rows", () => {
  assert.equal(catalogue.length, 506);
  assert.equal(Object.keys(specCounts).length, 31);
  assert.equal(new Set(catalogue.map((x) => x.packet)).size, 11);
  assert.equal(
    new Set(catalogue.map((x) => `${x.file}\0${x.title}`)).size,
    506,
  );
  assert.ok(catalogue.every((x) => x.fullRows.length === 0));
  assert.equal(catalogue.filter((x) => x.disposition === "DARK").length, 36);
  assert.equal(
    catalogue.filter((x) => x.kind === "admission-guard").length,
    32,
  );
});

test("detector/fixture defaults preserve both browser identities without browser/tenant coverage", () => {
  const plan = invocationPlans(input);
  assert.equal(plan.length, 868);
  assert.equal(
    new Set(plan.map((x) => x.consumerInvocation.id)).size,
    plan.length,
  );
  assert.ok(
    plan.every(
      (x) => x.binding.viewport === null && x.binding.tenants.length === 0,
    ),
  );
  assert.ok(
    plan.every(
      (x) =>
        x.binding.profile === "none" &&
        x.binding.network ===
          catalogue.find((row) => row.id === x.binding.expectedIds[0]).network,
    ),
  );
  assert.ok(
    plan.every(
      (x) =>
        x.consumerInvocation.argv.filter((a) => a.startsWith("--reporter="))
          .length === 1,
    ),
  );
  assert.equal(discoveryInvocations(input).length, 2);
  const core = orderedInvocations(input);
  assert.equal(core.length, 870);
  assert.ok(core.slice(0, 2).every((row) => row.argv.includes("--list")));
  assert.deepEqual(Object.keys(core[0]).sort(), [
    "argv",
    "browser",
    "cwd",
    "id",
    "network",
    "type",
  ]);
});

test("missing/extra/duplicate identities and full-row mislabelling fail closed", () => {
  const expected = catalogue.slice(0, 2);
  const observed = expected.map((x) => ({
    file: x.file,
    title: x.title,
    browser: "chromium",
    project: "",
    parentType: "file",
    annotations: x.annotationTypes.map((type) => ({ type })),
    expectedStatus: "passed",
  }));
  assert.doesNotThrow(() => reconcileInventory(expected, observed, "chromium"));
  assert.throws(
    () => reconcileInventory(expected, [], "chromium"),
    /inventory/,
  );
  assert.throws(
    () => reconcileInventory(expected, [...observed, observed[0]], "chromium"),
    /inventory/,
  );
  assert.throws(
    () => reconcileInventory(expected, [observed[0], observed[0]], "chromium"),
    /inventory/,
  );
  assert.throws(
    () =>
      reconcileInventory(
        expected,
        [
          { ...observed[0], annotations: [{ type: "journey-assertion" }] },
          observed[1],
        ],
        "chromium",
      ),
    /annotation/,
  );
});

test("runner cannot turn owed guards or DARK scope into executable positives", () => {
  assert.throws(
    () =>
      invocationPlans({
        ...input,
        includeIds: [catalogue.find((x) => x.disposition === "DARK").id],
      }),
    /DARK/,
  );
  assert.throws(
    () =>
      invocationPlans({
        ...input,
        includeIds: [catalogue.find((x) => x.kind === "admission-guard").id],
      }),
    /OWED/,
  );
  assert.throws(
    () => invocationPlans({ ...input, browsers: ["webkit"] }),
    /browser/,
  );
  assert.throws(
    () => invocationPlans({ ...input, browsers: ["chromium"] }),
    /browser/,
  );
});

test("red requires native assertion, exact step, terminal and no skip/retry/setup failure", () => {
  const row = catalogue.find((x) => x.packet === "J11" && x.phase === "red");
  const facts = {
    starts: 1,
    ends: 1,
    retry: 0,
    status: "failed",
    expectedStatus: "failed",
    errorCount: 1,
    intendedAssertion: true,
    fullRowAttachments: 0,
    notWalked: false,
    stepCounts: Object.fromEntries(row.steps.map((step) => [step, 1])),
  };
  assert.doesNotThrow(() => reconcileTerminal(row, facts));
  for (const bad of [
    { intendedAssertion: false },
    { status: "passed" },
    { status: "skipped" },
    { retry: 1 },
    { starts: 0 },
    { ends: 2 },
    { fullRowAttachments: 1 },
    { notWalked: true },
  ]) {
    assert.throws(
      () => reconcileTerminal(row, { ...facts, ...bad }),
      /terminal/,
    );
  }
});

test("native argv permits only additive reporters, never browser/project or phase overrides", () => {
  const plan = invocationPlans(input)[0];
  const argv = [
    "node",
    "/accepted/cli.js",
    ...plan.consumerInvocation.argv.slice(2),
  ];
  assert.doesNotThrow(() => validateArguments(plan.binding, argv));
  assert.doesNotThrow(() =>
    validateArguments(
      plan.binding,
      argv.map((arg) =>
        arg.startsWith("--reporter=") ? `${arg},/accepted/additional.cjs` : arg,
      ),
    ),
  );
  assert.throws(
    () => validateArguments(plan.binding, [...argv, "--browser=webkit"]),
    /argv drift/,
  );
  assert.throws(
    () =>
      validateArguments(
        plan.binding,
        argv.filter((arg) => !arg.startsWith("--grep=")),
      ),
    /argv drift/,
  );
});

test("binding drift and nonlocal anonymous fragment endpoints are refused", () => {
  const plan = invocationPlans(input)[0];
  assert.doesNotThrow(() => validateBinding(plan.binding));
  assert.throws(
    () => validateBinding({ ...plan.binding, expectedIds: [] }),
    /binding/,
  );
  const fragment = catalogue.find((x) => x.kind === "product-fragment");
  assert.throws(
    () => invocationPlans({ ...input, includeIds: [fragment.id] }),
    /width/,
  );
  assert.throws(
    () =>
      invocationPlans({
        ...input,
        includeIds: [fragment.id],
        stockFragment: {
          width: 1440,
          height: 900,
          widthAuthority: "test-only",
          appUrl: "https://example.invalid",
          gatewayUrl: "http://localhost:28090",
        },
      }),
    /loopback/,
  );
});
