// Integration test run by each stack's report job after Playwright list and merge.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { collectSpecs } from "./shard-report.mjs";

const stack = process.env.E2E_STACK;
const cases = [
  ["harness/managed-mock.spec.ts", "managed background routing has source, serving health and owned poller proof"],
  ["flows/settings/host-update-status.spec.ts", "SET-E2E-002: admin reads fresh host update reports and missing reporting stays unknown"],
];
for (const [file, title] of cases) {
  test(`${stack}: ${title}`, () => {
    assert.ok(["distributed", "standalone"].includes(stack));
    const expected = stack === "distributed" ? "passed" : "skipped";
    const select = (path) => collectSpecs(JSON.parse(readFileSync(path, "utf8")))
      .filter(spec => spec.file === file && JSON.parse(spec.key)[3] === title);
    const listed = select("listed-suite.json");
    assert.ok(listed.length > 0, `listing missing ${file}: ${title}`);
    assert.ok(listed.every(spec => spec.expectedStatus === expected));
    const actual = select("merged-report.json");
    assert.deepEqual(actual.map(spec => spec.key).sort(), listed.map(spec => spec.key).sort());
    for (const spec of actual) assert.deepEqual(spec.statuses, [expected]);
  });
}
