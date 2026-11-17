"use strict";
// CUMULATIVE-F01-01 regression source. NOT RUN locally; accepted guarded runner
// must also perform actual full-tree Playwright --list reconciliation in BOTH browsers.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { catalogue, specCounts } = require("./catalogue.cjs");
const {
  discoveryInvocations,
  invocationPlans,
  validateBinding,
} = require("./consumer.cjs");
const { reconcileInventory, reconcileTerminal } = require("./reconcile.cjs");
const input = {
  sourceRef: "f59b5541cfdbe747fa26007f2c71501e05924d69",
  outputRoot: "/landing/cumulative-f01-contract",
  browsers: ["chromium", "firefox"],
};
const observed = (rows, browser) =>
  rows.map((row) => ({
    file: row.file,
    title: row.title,
    annotations: row.annotationTypes.map((type) => ({ type })),
    browser,
    project: "",
    parentType: "file",
    expectedStatus: "passed",
  }));

test("full spec-path census includes the two uncalled registrar modules", () => {
  function files(dir) {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
      if (item.name === "_invocation") return [];
      const file = path.join(dir, item.name);
      return item.isDirectory()
        ? files(file)
        : file.endsWith(".spec.ts")
          ? [file]
          : [];
    });
  }
  const root = path.resolve(__dirname, "..");
  assert.deepEqual(
    files(root)
      .map(
        (file) =>
          `journeys/gateway/${path.relative(root, file).split(path.sep).join("/")}`,
      )
      .sort(),
    Object.keys(specCounts).sort(),
  );
  assert.deepEqual(
    Object.entries(specCounts)
      .filter(([, count]) => count === 0)
      .map(([file]) => file),
    [
      "journeys/gateway/J27-L3/redis-recovery.spec.ts",
      "journeys/gateway/J27-L4/gateway-lifecycle.spec.ts",
    ],
  );
  assert.equal(
    Object.values(specCounts).reduce((a, b) => a + b, 0),
    506,
  );
  for (const [file, count] of Object.entries(specCounts)) {
    assert.equal(
      catalogue.filter((row) => row.file === file).length,
      count,
      file,
    );
  }
});

test("the original 318 alone cannot satisfy mandatory cumulative discovery", () => {
  for (const plan of discoveryInvocations(input)) {
    assert.deepEqual(
      plan.binding.expectedIds,
      catalogue.map((row) => row.id),
    );
    assert.equal(plan.binding.network, "none"); // --list executes no callbacks/contexts.
    const native = observed(catalogue, plan.binding.browser);
    assert.doesNotThrow(() =>
      reconcileInventory(catalogue, native, plan.binding.browser),
    );
    assert.throws(
      () =>
        reconcileInventory(
          catalogue,
          native.slice(0, 318),
          plan.binding.browser,
        ),
      /inventory/,
    );
    assert.throws(
      () =>
        reconcileInventory(
          catalogue,
          [...native, native.at(-1)],
          plan.binding.browser,
        ),
      /inventory/,
    );
    assert.throws(
      () =>
        reconcileInventory(
          catalogue,
          [native[0], ...native.slice(0, -1)],
          plan.binding.browser,
        ),
      /inventory/,
    );
    assert.throws(
      () =>
        reconcileInventory(
          catalogue,
          [
            ...native.slice(0, -1),
            { ...native.at(-1), title: "uncatalogued addition" },
          ],
          plan.binding.browser,
        ),
      /inventory/,
    );
    assert.throws(
      () =>
        reconcileInventory(
          catalogue,
          [
            ...native.slice(0, -1),
            { ...native.at(-1), expectedStatus: "skipped" },
          ],
          plan.binding.browser,
        ),
      /expected-status/,
    );
  }
});

test("loopback fixtures are not network-none detectors or product rows", () => {
  const fixtures = catalogue.filter((row) => row.kind === "fixture-only");
  assert.equal(fixtures.length, 148);
  assert.equal(fixtures.filter((row) => row.network === "own-host").length, 41);
  assert.equal(fixtures.filter((row) => row.browserContextUsed).length, 2);
  assert.ok(
    fixtures.every(
      (row) => row.fullRows.length === 0 && row.expectedStatus === "passed",
    ),
  );
  const plans = invocationPlans(input);
  assert.equal(
    plans.filter((plan) => plan.binding.network === "own-host").length,
    78,
  );
  for (const plan of plans) {
    const row = catalogue.find(
      (item) => item.id === plan.binding.expectedIds[0],
    );
    assert.equal(plan.binding.network, row.network);
    assert.equal(plan.consumerInvocation.network, row.network);
    assert.equal(row.browserContextUsed, false);
    assert.doesNotThrow(() => validateBinding(plan.binding));
    assert.throws(
      () =>
        validateBinding({
          ...plan.binding,
          network: row.network === "none" ? "own-host" : "none",
        }),
      /network/,
    );
  }
});

test("fixture browsers require admitted viewport without fabricated stock app bindings", () => {
  for (const row of catalogue.filter(
    (item) => item.kind === "fixture-only" && item.browserContextUsed,
  )) {
    assert.throws(
      () => invocationPlans({ ...input, includeIds: [row.id] }),
      /width/,
    );
    const plans = invocationPlans({
      ...input,
      includeIds: [row.id],
      fixtureBrowser: {
        width: 1440,
        height: 900,
        widthAuthority: "unit-only-not-runtime-admission",
      },
    });
    assert.equal(plans.length, 2);
    for (const plan of plans) {
      assert.deepEqual(plan.binding.viewport, { width: 1440, height: 900 });
      assert.equal(plan.binding.network, "own-host");
      assert.equal(plan.binding.profile, "none");
      assert.equal(plan.binding.stack, "none");
      assert.equal(plan.binding.appUrl, "");
      assert.equal(plan.binding.gatewayUrl, "");
      assert.deepEqual(Object.keys(plan.environment), ["GW_JOURNEY_BINDING"]);
      assert.doesNotThrow(() => validateBinding(plan.binding));
      assert.throws(
        () => validateBinding({ ...plan.binding, viewport: null }),
        /width/,
      );
      assert.throws(
        () =>
          validateBinding({ ...plan.binding, appUrl: "http://127.0.0.1:8100" }),
        /product endpoints/,
      );
    }
  }
});

test("unbound Temporal product declaration is inventoried but never dispatchable", () => {
  const rows = catalogue.filter((row) => row.kind === "runtime-row");
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(
    row.title,
    "GW-JOURNEY-J27-L6.registration.full-inventory [baseline]",
  );
  assert.deepEqual(row.annotationTypes, ["journey-assertion"]);
  assert.equal(row.disposition, "OWED");
  assert.deepEqual(row.fullRows, []);
  assert.throws(
    () => invocationPlans({ ...input, includeIds: [row.id] }),
    /OWED/,
  );
  const binding = invocationPlans(input)[0].binding;
  assert.throws(
    () => validateBinding({ ...binding, expectedIds: [row.id] }),
    /OWED/,
  );
  assert.throws(
    () =>
      reconcileTerminal(row, {
        starts: 1,
        ends: 1,
        retry: 0,
        fullRowAttachments: 0,
        notWalked: false,
        stepCounts: Object.fromEntries(row.steps.map((step) => [step, 1])),
        status: "passed",
        expectedStatus: "passed",
        errorCount: 0,
      }),
    /terminal/,
  );
});

test("new native reds retain their exact assertion step and failure classification", () => {
  const rows = catalogue.filter((row) =>
    /\/(?:stream|identity)-controls\.spec\.ts$/.test(row.file),
  );
  assert.equal(rows.length, 39);
  assert.equal(rows.filter((row) => row.phase === "red").length, 13);
  for (const row of rows.filter((item) => item.phase === "red")) {
    assert.equal(row.expectedStatus, "failed");
    assert.ok(row.annotationTypes.includes("money-intended-failure"));
    assert.equal(row.steps.length, 1);
    const facts = {
      starts: 1,
      ends: 1,
      retry: 0,
      fullRowAttachments: 0,
      notWalked: false,
      stepCounts: { [row.steps[0]]: 1 },
      status: "failed",
      expectedStatus: "failed",
      errorCount: 1,
      intendedAssertion: true,
    };
    assert.doesNotThrow(() => reconcileTerminal(row, facts));
    for (const defect of [
      { intendedAssertion: false },
      { stepCounts: {} },
      { errorCount: 2 },
      { status: "passed" },
      { status: "skipped" },
      { fullRowAttachments: 1 },
    ]) {
      assert.throws(
        () => reconcileTerminal(row, { ...facts, ...defect }),
        /terminal/,
      );
    }
  }
});
