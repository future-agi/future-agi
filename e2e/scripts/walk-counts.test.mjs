import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import CountsReporter from "../walks/vm/counts-reporter.cjs";

test("walk counts contain statuses and totals, never test bodies or attachments", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "walk-counts-"));
  try {
    const outputFile = path.join(dir, "counts.json");
    const reporter = new CountsReporter({ outputFile });
    reporter.onBegin({}, { allTests: () => [1, 2, 3] });
    const sensitive = {
      title: "synthetic-credential-shaped-value",
      attachments: ["must-stay-on-vm"],
    };
    reporter.onTestEnd(sensitive, { status: "passed", ...sensitive });
    reporter.onTestEnd(sensitive, { status: "failed", ...sensitive });
    reporter.onTestEnd(sensitive, { status: "skipped", ...sensitive });
    reporter.onError(sensitive);
    reporter.onEnd({ status: "failed", duration: 1234, ...sensitive });
    assert.deepEqual(JSON.parse(readFileSync(outputFile, "utf8")), {
      planned: 3,
      completed: 3,
      passed: 1,
      failed: 1,
      timedOut: 0,
      skipped: 1,
      interrupted: 0,
      errors: 1,
      status: "failed",
      durationMs: 1234,
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("walk counts retain failing source locations without copying error values", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "walk-counts-"));
  try {
    const outputFile = path.join(dir, "counts.json");
    const reporter = new CountsReporter({ outputFile });
    reporter.onTestEnd(
      {},
      {
        status: "failed",
        errors: [
          {
            message: "synthetic-private-value",
            stack:
              "Error: synthetic-private-value\n    at run (/fixture/e2e/lib/redis-probe.ts:146:8)\n    at /fixture/e2e/flows/walks/default-off-census.spec.ts:132:9",
          },
        ],
      },
    );
    reporter.onEnd({ status: "failed", duration: 1 });
    const counts = JSON.parse(readFileSync(outputFile, "utf8"));
    assert.deepEqual(counts.failures, [
      {
        status: "failed",
        frames: [
          { file: "e2e/lib/redis-probe.ts", line: 146, column: 8 },
          {
            file: "e2e/flows/walks/default-off-census.spec.ts",
            line: 132,
            column: 9,
          },
        ],
      },
    ]);
    assert.equal(
      JSON.stringify(counts).includes("synthetic-private-value"),
      false,
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("walk counts require an explicit output path and ignore unrecognized status text", () => {
  const prior = process.env.E2E_WALK_COUNTS;
  delete process.env.E2E_WALK_COUNTS;
  try {
    assert.throws(() => new CountsReporter(), /outputFile/);
  } finally {
    if (prior !== undefined) process.env.E2E_WALK_COUNTS = prior;
  }
  const dir = mkdtempSync(path.join(os.tmpdir(), "walk-counts-"));
  try {
    const outputFile = path.join(dir, "counts.json");
    const reporter = new CountsReporter({ outputFile });
    reporter.onTestEnd({}, { status: "synthetic-credential-shaped-value" });
    reporter.onEnd({ status: "unrecognized-private-value", duration: NaN });
    const counts = JSON.parse(readFileSync(outputFile, "utf8"));
    assert.equal(counts.status, "unknown");
    assert.equal(counts.completed, 0);
    assert.equal(counts.durationMs, 0);
    assert.equal(JSON.stringify(counts).includes("credential"), false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
