import assert from "node:assert/strict";
import { test } from "node:test";
import { packCanonical } from "../src/pack.mjs";
import { STEPS, checkSubmission } from "../src/pipeline.mjs";
import { CHECKER, fixture, job, passingSandbox, registry, upload } from "./helpers.mjs";

const run = (files, { context, previous, tier = "community" } = {}) => checkSubmission({
  job: job({ publisherTier: tier, hints: { name: "good-agent" } }), raw: upload(files),
  registry: registry(context, previous), sandbox: passingSandbox, checker: CHECKER,
});

test("good reaches in_review with every step and a canonical bundle", async () => {
  const { report, bundle } = await run(fixture("good"));
  assert.equal(report.schema, "omega-hub-check-report/v1");
  assert.equal(report.outcome, "in_review");
  assert.deepEqual(report.steps.map((step) => step.id), STEPS);
  assert.equal(report.manifest.id, "good-agent");
  assert.match(report.digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(report.bundleSha256, packCanonical(fixture("good")).bundleSha256);
  assert.ok(bundle.length === report.bundleBytes);
  const capabilities = report.steps.find((step) => step.id === "capabilities");
  assert.deepEqual(capabilities.result, { cli: true, openInStudio: false, deploy: false, consoleTemplate: false });
});

test("a blocked submission fails and never reaches the sandbox", async () => {
  let ran = false;
  const files = fixture("eval");
  const { report } = await checkSubmission({ job: job(), raw: upload(files), registry: registry(), checker: CHECKER,
    sandbox: async () => { ran = true; return passingSandbox(); } });
  assert.equal(report.outcome, "checks_failed");
  assert.equal(ran, false);
  assert.equal(report.steps.find((step) => step.id === "sandbox-run").status, "skipped");
});

test("an unpack failure reports and carries no bundle", async () => {
  const { report, bundle } = await checkSubmission({ job: job(), raw: Buffer.from("not an archive"), registry: registry(), sandbox: passingSandbox, checker: CHECKER });
  assert.equal(report.outcome, "checks_failed");
  assert.equal(bundle, null);
  assert.equal(report.steps[0].findings[0].rule, "archive");
});

test("an update is diffed against the previous version, new flags first", async () => {
  const previous = packCanonical(fixture("good")).bundle;
  const files = fixture("child-process");
  const next = JSON.parse(files.get("omega.hub.json").toString("utf8"));
  files.set("omega.hub.json", Buffer.from(JSON.stringify({ ...next, version: "1.1.0" }, null, 2)));
  const { report } = await run(files, { previous });
  const diff = report.steps.find((step) => step.id === "diff").diff;
  assert.deepEqual(diff.files.added, ["helper.mjs"]);
  assert.ok(diff.manifest.some((change) => change.field === "version"));
  assert.ok(diff.highlights.some((line) => line.startsWith("new scan flags: child-process")));
  assert.ok(diff.text["helper.mjs"].includes("+1: import { execFileSync }"));
});
