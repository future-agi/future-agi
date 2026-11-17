"use strict";
// F01-SOL-01 regression controls. Authored only; execute in the accepted guarded
// runner with the e2e/yarn.lock Playwright 1.62.1 installation, never locally.
const test = require("node:test");
const assert = require("node:assert/strict");
const { expect } = require("@playwright/test");
const JourneyReporter = require("./reporter.cjs");
const { catalogue } = require("./catalogue.cjs");
const { reconcileTerminal } = require("./reconcile.cjs");

// Provenance: f93d4fe464 J13/oracle-cases.ts and oracle-assertions.ts
// assertDailyCap/assertActivationTransition/assertMemberNoWrite. These seven
// W04 controls use toStrictEqual; caps-dispatch uses toBe and is not this defect.
const strictCases = [
  "caps-byok",
  "caps-scope",
  "caps-utc",
  "revision-activation",
  "revision-increase",
  "revision-copy",
  "member-version",
];
function rowFor(id, phase) {
  const title = `GW-JOURNEY-J13.oracle.${id} [${phase}]`;
  const rows = catalogue.filter(
    (row) => row.packet === "J13" && row.title === title,
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].disposition, "SOURCE_ONLY");
  assert.equal(rows[0].steps.length, 1);
  return rows[0];
}
function strictCheck(marker, observed) {
  // Synthetic reporter-classifier input, not a product observation or fixture.
  expect({ count: observed }, marker).toStrictEqual({ count: 0 });
}
function nativeError(marker) {
  let captured;
  assert.throws(
    () => strictCheck(marker, 1),
    (error) => {
      assert.match(error.message, /toStrictEqual/);
      assert.ok(error.message.includes(marker));
      captured = { message: error.message };
      return true;
    },
  );
  return captured;
}

// Callback-shape unit fixture only: no binding files, filesystem writes,
// reporter constructor/onEnd, subprocesses or runtime qualification. The actual
// callbacks and reconciliation run unmodified. Native CLI delivery, constructor
// admission and all base reporters remain separate F01-R05 runner obligations.
// Shape provenance: Playwright 1.62.1 lib/matchers/expect.js:callMatcherAsStep
// emits category=expect and a native ExpectError; J13/oracle-controls.spec.ts
// nests it under the named test.step. The error text above comes from the real
// locked matcher, not a hand-written approximation of its formatted message.
function start(row) {
  const reporter = Object.create(JourneyReporter.prototype);
  reporter.binding = { mode: "execute" };
  reporter.tests = new Map([[row.id, row]]);
  reporter.facts = new Map();
  reporter.errorCount = 0;
  const nativeTest = {
    id: row.id,
    expectedStatus: row.expectedStatus,
    annotations: [],
  };
  const assertionStep = { title: row.steps[0], category: "test.step" };
  reporter.onTestBegin(nativeTest, { retry: 0 });
  return { reporter, nativeTest, assertionStep, row };
}
function expectEnd(context, error, overrides = {}) {
  context.reporter.onStepEnd(
    context.nativeTest,
    {},
    {
      title: context.row.failureMarker,
      category: "expect",
      parent: context.assertionStep,
      error,
      ...overrides,
    },
  );
}
function finish(context, error) {
  const { reporter, nativeTest, assertionStep } = context;
  reporter.onStepEnd(nativeTest, {}, assertionStep);
  reporter.onTestEnd(nativeTest, {
    retry: 0,
    status: error ? "failed" : "passed",
    errors: error ? [error] : [],
    attachments: [],
  });
  assert.equal(reporter.errorCount, 0);
  return reporter.facts.get(nativeTest.id);
}

test("all seven J13 W04 strict-equality reds reach their intended assertion", () => {
  const actual = catalogue.filter(
    (row) =>
      row.packet === "J13" &&
      row.phase === "red" &&
      row.relatedRows.some((id) => id.startsWith("W04-")) &&
      row.failureMarker !== "J13/oracle/caps-dispatch",
  );
  assert.deepEqual(
    actual.map((row) => row.title).sort(),
    strictCases.map((id) => `GW-JOURNEY-J13.oracle.${id} [red]`).sort(),
  );
  for (const id of strictCases) {
    const row = rowFor(id, "red");
    const context = start(row);
    const error = nativeError(row.failureMarker);
    expectEnd(context, error);
    const facts = finish(context, error);
    assert.equal(facts.intendedAssertion, true, id);
    assert.deepEqual(facts.stepCounts, { [row.steps[0]]: 1 });
    assert.doesNotThrow(() => reconcileTerminal(row, facts));
  }
});

test("strict-equality failures outside the exact step/marker are not intended reds", () => {
  const row = rowFor("caps-byok", "red");
  const error = nativeError(row.failureMarker);
  for (const overrides of [
    { parent: { title: "unrelated assertion", category: "test.step" } },
    {
      parent: {
        title: row.steps[0].replace(/\/red$/, "/healthy-precheck"),
        category: "test.step",
      },
    },
    { parent: { title: "beforeEach hook", category: "hook" } },
    { parent: undefined },
    { category: "hook" },
    { category: "test.step", title: "setup threw an error" },
    { error: nativeError("J13/oracle/caps-scope") },
    { error: undefined },
  ]) {
    const context = start(row);
    expectEnd(context, error, overrides);
    const facts = finish(context, overrides.error || error);
    assert.equal(facts.intendedAssertion, false);
    assert.throws(
      () => reconcileTerminal(row, facts),
      /intended native assertion/,
    );
  }
});

test("intended strict-equality reds still require exact native terminals and step counts", () => {
  const row = rowFor("caps-byok", "red");
  const context = start(row);
  const error = nativeError(row.failureMarker);
  expectEnd(context, error);
  const facts = finish(context, error);
  assert.equal(facts.intendedAssertion, true);
  assert.doesNotThrow(() => reconcileTerminal(row, facts));
  for (const bad of [
    { expectedStatus: "passed" },
    { status: "passed" },
    { status: "skipped" },
    { status: "timedOut" },
    { status: "interrupted" },
    { errorCount: 0 },
    { errorCount: 2 },
    { starts: 0 },
    { ends: 2 },
    { retry: 1 },
    { stepCounts: {} },
    { stepCounts: { [row.steps[0]]: 2 } },
    { fullRowAttachments: 1 },
    { notWalked: true },
  ]) {
    assert.throws(
      () => reconcileTerminal(row, { ...facts, ...bad }),
      /terminal/,
    );
  }
});

test("the seven unchanged strict-equality baselines and restored positives stay green", () => {
  for (const id of strictCases) {
    for (const phase of ["baseline", "restored"]) {
      const row = rowFor(id, phase);
      const context = start(row);
      strictCheck(row.failureMarker, 0);
      expectEnd(context, undefined);
      const facts = finish(context, undefined);
      assert.equal(facts.intendedAssertion, false);
      assert.equal(facts.errorCount, 0);
      assert.deepEqual(facts.stepCounts, { [row.steps[0]]: 1 });
      assert.doesNotThrow(() => reconcileTerminal(row, facts));
    }
  }
});
