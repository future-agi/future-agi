"use strict";
// Additional E3 reporter only. Never dispatches, rewrites native streams or
// promotes Playwright status. Original errors remain with accepted base reporters.
const fs = require("node:fs");
const path = require("node:path");
const { catalogue } = require("./catalogue.cjs");
const { readBinding, validateArguments } = require("./consumer.cjs");
const { reconcileInventory, reconcileTerminal } = require("./reconcile.cjs");

class JourneyReporter {
  constructor(options = {}) {
    this.binding = readBinding();
    validateArguments(this.binding, process.argv);
    this.expected = this.binding.expectedIds.map((id) =>
      catalogue.find((item) => item.id === id),
    );
    this.outputFile =
      options.outputFile ||
      path.join(this.binding.outputRoot, "reconciliation.json");
    if (
      this.outputFile !==
        path.join(this.binding.outputRoot, "reconciliation.json") ||
      fs.realpathSync(this.binding.outputRoot) !== this.binding.outputRoot ||
      fs.existsSync(this.outputFile)
    ) {
      throw new Error(
        "F01 reporter requires a fresh output in its canonical landing directory",
      );
    }
    this.began = false;
    this.badInventory = false;
    this.errorCount = 0;
    this.facts = new Map();
    this.tests = new Map();
  }
  printsToStdio() {
    return false;
  }
  onBegin(config, suite) {
    this.began = true;
    const e2eRoot = path.resolve(__dirname, "../../..");
    const native = suite.allTests();
    const observed = native.map((item) => ({
      file: path
        .relative(e2eRoot, item.location.file)
        .split(path.sep)
        .join("/"),
      title: item.title,
      browser: item.parent.project()?.use.browserName,
      project: item.parent.project()?.name,
      annotations: item.annotations,
      parentType: item.parent.type,
      expectedStatus: item.expectedStatus,
    }));
    try {
      if (
        config.workers !== 1 ||
        config.projects.length !== 1 ||
        config.projects[0].retries !== 0 ||
        config.projects[0].repeatEach !== 1 ||
        config.shard !== null ||
        config.forbidOnly !== true
      ) {
        throw new Error("F01 native configuration drift");
      }
      reconcileInventory(this.expected, observed, this.binding.browser);
      native.forEach((test, index) => {
        const item = this.expected.find(
          (x) => x.file === observed[index].file && x.title === test.title,
        );
        this.tests.set(test.id, item);
      });
    } catch {
      // No raw error/body/header values in this additional projection. Native
      // stdout/stderr and errors are neither intercepted nor modified.
      this.badInventory = true;
    }
  }
  onTestBegin(test, result) {
    if (!this.tests.has(test.id) || this.binding.mode !== "execute") {
      this.errorCount++;
      return;
    }
    const prior = this.facts.get(test.id);
    this.facts.set(
      test.id,
      prior
        ? { ...prior, starts: prior.starts + 1 }
        : {
            starts: 1,
            ends: 0,
            retry: result.retry,
            status: "",
            expectedStatus: "",
            errorCount: 0,
            intendedAssertion: false,
            fullRowAttachments: 0,
            notWalked: false,
            stepCounts: {},
          },
    );
  }
  onStepEnd(test, _result, step) {
    const item = this.tests.get(test.id);
    const facts = this.facts.get(test.id);
    if (!item || !facts) {
      this.errorCount++;
      return;
    }
    if (step.category === "test.step" && item.steps.includes(step.title)) {
      facts.stepCounts[step.title] = (facts.stepCounts[step.title] || 0) + 1;
    }
    if (
      item.expectedStatus !== "failed" ||
      step.category !== "expect" ||
      !step.error
    )
      return;
    const chain = [];
    for (let current = step.parent; current; current = current.parent)
      chain.push(current.title);
    // Require the actual native expect callback inside the exact inventoried
    // assertion step, not setup, a thrown Error, an attachment failure or timeout.
    const message = step.error.message || "";
    if (
      chain.some((title) => item.steps.includes(title)) &&
      // Native expect event is authoritative; allow ANSI between formatter
      // tokens without rewriting or stripping a single captured byte.
      // f93d4fe464 J13/oracle-assertions.ts uses toStrictEqual for seven W04 reds.
      message.includes("expect") &&
      /to(?:StrictEqual|Equal|Be|HaveLength)/.test(message) &&
      (!item.failureMarker || message.includes(item.failureMarker))
    )
      facts.intendedAssertion = true;
  }
  onTestEnd(test, result) {
    const facts = this.facts.get(test.id);
    if (!facts) {
      this.errorCount++;
      return;
    }
    facts.ends++;
    facts.retry = result.retry;
    facts.status = result.status;
    facts.expectedStatus = test.expectedStatus;
    facts.errorCount = result.errors.length;
    // Current inventory covers zero full product rows. Standard row events or
    // annotations are a classification change, not silently accepted coverage.
    facts.fullRowAttachments =
      result.attachments.filter((a) => a.name === "journey-row").length +
      test.annotations.filter((a) => a.type === "journey-assertion").length;
    facts.notWalked = result.attachments.some(
      (a) => a.name === "journey-not-walked",
    );
  }
  onError() {
    this.errorCount++;
  }
  onEnd(result) {
    let failed =
      !this.began ||
      this.badInventory ||
      this.errorCount !== 0 ||
      result.status !== "passed";
    if (this.binding.mode === "discover") {
      if (this.facts.size !== 0) failed = true;
    } else {
      if (this.facts.size !== this.expected.length) failed = true;
      for (const [id, item] of this.tests) {
        try {
          reconcileTerminal(item, this.facts.get(id) || {});
        } catch {
          failed = true;
        }
      }
    }
    const record = {
      schema: "gateway-journey-reconciliation/v1",
      invocationId: this.binding.id,
      sourceRef: this.binding.sourceRef,
      browser: this.binding.browser,
      mode: this.binding.mode,
      nativeStatus: result.status,
      reconciliation: failed ? "REFUSED" : "MATCHED",
      expectedTestCount: this.expected.length,
      observedTestCount: this.tests.size,
      tests: [...this.tests].map(([id, item]) => ({
        id: item.id,
        file: item.file,
        title: item.title,
        phase: item.phase,
        kind: item.kind,
        relatedRows: item.relatedRows,
        facts: this.facts.get(id) || null,
      })),
      // A MATCHED detector is not WALKED_PASS or acceptance of a full row.
      completeProductRows: [],
      productAccepted: false,
      productWalkStatus: "NOT_WALKED",
      independentRunnerCertification: "OWED",
    };
    fs.writeFileSync(this.outputFile, `${JSON.stringify(record, null, 2)}\n`, {
      flag: "wx",
      mode: 0o600,
    });
    if (failed) return { status: "failed" }; // only demote; never return 'passed'
  }
}
module.exports = JourneyReporter;
