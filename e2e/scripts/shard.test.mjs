import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import { parse as parseYaml } from "yaml";
import {
  SPEC_GLOBS,
  listSpecFiles,
  parseShard,
  planShards,
  readDurations,
  shardTestMatch,
} from "../lib/shard.ts";
import { grepInvertPattern } from "../lib/quarantine.ts";
import { carriedOverShards, checkParity, cohortAttempt, fileDurations } from "./shard-report.mjs";

const flat = (groups) => groups.flat().sort();

// A suite shaped like the real one: a few long flows, many near-instant harness files.
function suite() {
  const durations = {};
  const files = [];
  for (let i = 0; i < 40; i += 1) {
    files.push(`flows/area/flow-${i}.spec.ts`);
    durations[files.at(-1)] = 20 + ((i * 37) % 400);
  }
  for (let i = 0; i < 90; i += 1) {
    files.push(`harness/unit-${i}.spec.ts`);
    durations[files.at(-1)] = 0.2;
  }
  return { files, durations };
}

test("every spec file lands in exactly one shard, for any shard count", () => {
  const { files, durations } = suite();
  for (const total of [1, 2, 3, 4, 6, 8, 16]) {
    const groups = planShards(files, durations, total);
    assert.equal(groups.length, total);
    assert.deepEqual(flat(groups), [...files].sort(), `${total} shards`);
  }
});

test("the split balances time, not test or file count", () => {
  const { files, durations } = suite();
  const groups = planShards(files, durations, 4);
  const load = groups.map((group) =>
    group.reduce((sum, file) => sum + durations[file], 0),
  );
  const longest = Math.max(...Object.values(durations));
  // Longest-first to the least loaded group never leaves a gap above one file.
  assert.ok(
    Math.max(...load) - Math.min(...load) <= longest,
    JSON.stringify(load),
  );
  // Playwright's count-based --shard would put all 40 flows, and all the time, in the first of 4 shards.
  assert.ok(
    Math.max(...load) < 0.3 * load.reduce((a, b) => a + b, 0),
    JSON.stringify(load),
  );
});

test("the plan does not depend on the order the files are listed in", () => {
  const { files, durations } = suite();
  assert.deepEqual(
    planShards([...files].reverse(), durations, 4),
    planShards(files, durations, 4),
  );
});

test("a spec without a recorded duration still runs, weighted as the median file", () => {
  const durations = {
    "flows/a.spec.ts": 100,
    "flows/b.spec.ts": 100,
    "flows/c.spec.ts": 100,
  };
  const groups = planShards(
    [
      "flows/a.spec.ts",
      "flows/b.spec.ts",
      "flows/c.spec.ts",
      "flows/new.spec.ts",
    ],
    durations,
    2,
  );
  assert.deepEqual(
    groups.map((group) => group.length),
    [2, 2],
  );
  assert.deepEqual(
    flat(groups).filter((file) => file === "flows/new.spec.ts"),
    ["flows/new.spec.ts"],
  );
  // No durations at all: still a full partition.
  assert.deepEqual(
    flat(planShards(["flows/a.spec.ts", "flows/b.spec.ts"], {}, 2)),
    ["flows/a.spec.ts", "flows/b.spec.ts"],
  );
});

test("invalid plans and shard specs are refused", () => {
  assert.throws(
    () => planShards(["flows/a.spec.ts", "flows/a.spec.ts"], {}, 2),
    /duplicate/,
  );
  for (const total of [0, -1, 1.5, Number.NaN])
    assert.throws(
      () => planShards(["flows/a.spec.ts"], {}, total),
      /invalid shard count/,
    );
  for (const value of [
    "0/4",
    "5/4",
    "1",
    "1/0",
    "a/b",
    "1/4/2",
    " 1/4",
    "-1/4",
  ])
    assert.throws(() => parseShard(value), /E2E_SHARD/, value);
  assert.deepEqual(parseShard("2/4"), { index: 2, total: 4 });
  assert.equal(parseShard(undefined), undefined);
  assert.equal(parseShard(""), undefined);
});

function checkout(specs, durations) {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-shard-test-"));
  for (const root of ["flows", "harness"])
    mkdirSync(path.join(dir, root), { recursive: true });
  for (const spec of specs) {
    mkdirSync(path.dirname(path.join(dir, spec)), { recursive: true });
    writeFileSync(path.join(dir, spec), "");
  }
  const durationsFile = path.join(dir, ".test-durations.json");
  if (durations !== undefined)
    writeFileSync(
      durationsFile,
      typeof durations === "string" ? durations : JSON.stringify(durations),
    );
  return { dir, durationsFile };
}

test("E2E_SHARD selects one share of the files on disk and the shares cover all of them", (t) => {
  const specs = [
    "flows/a/one.spec.ts",
    "flows/a/two.spec.ts",
    "flows/b/three.spec.ts",
    "harness/four.spec.ts",
    "harness/deep/five.spec.ts",
  ];
  const { dir, durationsFile } = checkout(
    [
      ...specs,
      "flows/a/helper.ts",
      "harness/notes.md",
      "lib/not-a-root.spec.ts",
    ],
    {
      "flows/a/one.spec.ts": 300,
      "flows/a/two.spec.ts": 5,
      "flows/b/three.spec.ts": 5,
      "harness/four.spec.ts": 5,
    },
  );
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.deepEqual(listSpecFiles(dir), [...specs].sort());
  const shares = [1, 2, 3].map((index) =>
    shardTestMatch(`${index}/3`, dir, durationsFile),
  );
  assert.deepEqual(flat(shares), [...specs].sort());
  assert.deepEqual(
    shares[0],
    ["flows/a/one.spec.ts"],
    "the long file fills a shard by itself",
  );
  // Not sharded: Playwright keeps its own globs.
  assert.equal(shardTestMatch(undefined, dir, durationsFile), undefined);
  assert.equal(shardTestMatch("1/1", dir, durationsFile), undefined);
});

test("a shard that cannot be planned fails instead of running nothing", (t) => {
  const { dir, durationsFile } = checkout(
    ["flows/a.spec.ts", "harness/b.spec.ts"],
    {},
  );
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  assert.throws(
    () => shardTestMatch("3/3", dir, durationsFile),
    /has no spec files/,
  );
  assert.throws(
    () => shardTestMatch("1/2", dir, path.join(dir, "missing.json")),
    /ENOENT/,
  );
  const broken = checkout(["flows/a.spec.ts", "harness/b.spec.ts"], "[1, 2]");
  t.after(() => rmSync(broken.dir, { recursive: true, force: true }));
  assert.throws(
    () => shardTestMatch("1/2", broken.dir, broken.durationsFile),
    /must be an object/,
  );
  const glob = checkout(["flows/a[1].spec.ts", "harness/b.spec.ts"], {});
  t.after(() => rmSync(glob.dir, { recursive: true, force: true }));
  assert.throws(
    () => shardTestMatch("1/2", glob.dir, glob.durationsFile),
    /glob character/,
  );
});

test("the committed durations split the real spec files into CI shards of similar time", () => {
  const files = listSpecFiles();
  const durations = readDurations();
  const workflow = parseYaml(
    readFileSync(
      new URL("../../.github/workflows/e2e-ci.yml", import.meta.url),
      "utf8",
    ),
  );
  const total = Number(workflow.env.E2E_SHARDS);
  assert.ok(
    Number.isInteger(total) && total >= 2,
    `E2E_SHARDS=${workflow.env.E2E_SHARDS}`,
  );
  const groups = planShards(files, durations, total);
  assert.deepEqual(flat(groups), files);
  assert.ok(groups.every((group) => group.length > 0));
  for (const [file, seconds] of Object.entries(durations))
    assert.ok(Number.isFinite(seconds) && seconds >= 0, file);
  assert.deepEqual(SPEC_GLOBS, ["flows/**/*.spec.ts", "harness/**/*.spec.ts"]);
});

// --- parity ---------------------------------------------------------------

const spec = (file, line, title, results) => ({
  title,
  file,
  line,
  column: 1,
  tests: [
    { results: results.map((duration) => ({ status: "passed", duration })) },
  ],
});
const report = (...specs) => ({
  suites: [
    {
      title: "root",
      specs: specs.slice(0, 1),
      suites: [{ title: "nested", specs: specs.slice(1) }],
    },
  ],
});
const FILES = ["flows/a.spec.ts", "flows/b.spec.ts"];
const listed = report(
  spec("flows/a.spec.ts", 3, "one", []),
  spec("flows/a.spec.ts", 9, "two", []),
  spec("flows/b.spec.ts", 3, "one", []),
);

test("parity passes only when every listed test has exactly one result", () => {
  const merged = report(
    spec("flows/a.spec.ts", 3, "one", [1500]),
    spec("flows/a.spec.ts", 9, "two", [0]),
    spec("flows/b.spec.ts", 3, "one", [2500]),
  );
  const result = checkParity(listed, merged, FILES);
  assert.equal(result.ok, true);
  assert.deepEqual([result.listed, result.reported, result.files], [3, 3, 2]);
  assert.deepEqual(fileDurations(merged), {
    "flows/a.spec.ts": 1.5,
    "flows/b.spec.ts": 2.5,
  });
});

test("a report can reuse an unsuperseded cohort but not a missing or changed sibling", () => {
  const job = (i, attempt) => ({name: `e2e (distributed, ${i}/2)`, run_attempt: attempt});
  const jobs = [job(1, 1), job(2, 1)];
  assert.equal(cohortAttempt("distributed", 2, jobs), 1);
  assert.throws(() => cohortAttempt("distributed", 2, [jobs[0]]), /missing/);
  assert.throws(() => cohortAttempt("distributed", 2, [...jobs, job(2, 2)]), /mixed/);
  assert.equal(cohortAttempt("distributed", 2, [...jobs, job(2, 2), job(1, 2)]), 2);
});

test("a failed-jobs rerun is named as a mixed stack, not a missing receipt", () => {
  const blobs = (stack, attempt, shards) => shards.map(i => `blob-report-${stack}-${i}-attempt-${attempt}`);
  // Shard 4 of 6 reran; GitHub carries the other five over into attempt 2.
  const names = [...blobs("distributed", 1, [1, 2, 3, 4, 5, 6]), ...blobs("distributed", 2, [4]), "once-per-stack-distributed-attempt-1"];
  assert.deepEqual(carriedOverShards("distributed", 6, 2, names),
    ["1/6 (attempt 1)", "2/6 (attempt 1)", "3/6 (attempt 1)", "5/6 (attempt 1)", "6/6 (attempt 1)"]);
  // A full rerun, or the first attempt, is one stack.
  assert.deepEqual(carriedOverShards("distributed", 6, 2, [...names, ...blobs("distributed", 2, [1, 2, 3, 5, 6])]), []);
  assert.deepEqual(carriedOverShards("distributed", 6, 1, blobs("distributed", 1, [1, 2, 3, 4, 5, 6])), []);
  // A shard with no receipt at all is still the existing "missing receipt" failure, not a carry-over.
  assert.deepEqual(carriedOverShards("distributed", 2, 1, blobs("distributed", 1, [1])), []);
  // The other stack's receipts never count.
  assert.deepEqual(carriedOverShards("standalone", 2, 2, [...names, ...blobs("standalone", 2, [1, 2])]), []);
});

test("parity checks the listed project and declared outcome, not just result count", () => {
  const plan = spec("flows/a.spec.ts", 1, "one", []);
  const actual = spec("flows/a.spec.ts", 1, "one", [1]);
  plan.tests[0].projectId = actual.tests[0].projectId = "chromium";
  actual.tests[0].results[0].status = "skipped";
  actual.tests[0].expectedStatus = "skipped";
  for (const enforce of [false, true]) {
    const result = checkParity(report(plan), report(actual), FILES, enforce);
    assert.equal(result.ok, !enforce);
    assert.deepEqual(result.problems.runtimeSkipped, [JSON.stringify([FILES[0], 1, 1, "one", "chromium"])]);
    assert.deepEqual(result.problems.wrongOutcome, []);
  }
  // Other outcomes remain blocking, even with the runtime-skip gate off.
  for (const status of ["failed", "timedOut", "interrupted"]) {
    actual.tests[0].results[0].status = status;
    assert.equal(checkParity(report(plan), report(actual), FILES, false).ok, false);
  }
  actual.tests[0].results[0].status = "skipped";
  plan.tests[0].annotations = [{type: "fail"}];
  assert.equal(checkParity(report(plan), report(actual), FILES, false).ok, false);
  plan.tests[0].annotations = [{type: "skip", description: "not in this topology"}];
  const declared = checkParity(report(plan), report(actual), FILES);
  assert.equal(declared.ok, true);
  assert.deepEqual(declared.problems.runtimeSkipped, []);
  actual.tests[0].projectId = "different";
  assert.equal(checkParity(report(plan), report(actual), FILES, false).ok, false);
});

test("the phased parity CLI reports the same runtime skips in warning and enforced modes", (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-runtime-skips-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = listSpecFiles()[0];
  const actual = spec(file, 1, "runtime skip", [0]);
  actual.tests[0].results[0].status = actual.tests[0].expectedStatus = "skipped";
  writeFileSync(path.join(dir, "listed.json"), JSON.stringify(report(spec(file, 1, "runtime skip", []))));
  writeFileSync(path.join(dir, "merged.json"), JSON.stringify(report(actual)));
  for (const enforce of [false, true]) {
    const summary = path.join(dir, `summary-${enforce}`);
    const run = spawnSync(process.execPath, [
      fileURLToPath(new URL("./shard-report.mjs", import.meta.url)), "parity", "--phase-runtime-skips",
      "--listed", "listed.json", "--merged", "merged.json", "--out", "parity.json",
    ], { cwd: dir, encoding: "utf8", env: { ...process.env,
      GITHUB_STEP_SUMMARY: summary, E2E_ENFORCE_RUNTIME_SKIPS: enforce ? "true" : "",
    } });
    assert.equal(run.status, enforce ? 1 : 0, run.stderr);
    assert.match(run.stdout, enforce ? /::error .*runtimeSkipped/ : /::warning .*runtimeSkipped/);
    const receipt = JSON.parse(readFileSync(path.join(dir, "parity.json"), "utf8"));
    assert.equal(receipt.enforceRuntimeSkips, enforce);
    assert.equal(receipt.ok, !enforce);
    assert.deepEqual(receipt.problems.runtimeSkipped, [JSON.stringify([file, 1, 1, "runtime skip", ""])]);
    assert.ok(readFileSync(summary, "utf8").includes(receipt.problems.runtimeSkipped[0]));
  }
});

test("parity names a test that no shard ran, ran twice, never started or was not listed", () => {
  const one = spec("flows/a.spec.ts", 3, "one", [1]);
  const two = spec("flows/a.spec.ts", 9, "two", [1]);
  const three = spec("flows/b.spec.ts", 3, "one", [1]);
  const cases = [
    ["missing", report(one, two)],
    [
      "extra",
      report(one, two, three, spec("flows/c.spec.ts", 1, "stray", [1])),
    ],
    ["notRun", report(one, two, spec("flows/b.spec.ts", 3, "one", []))],
    ["duplicated", report(one, two, three, structuredClone(three))],
    ["duplicated", report(one, two, spec("flows/b.spec.ts", 3, "one", [1, 1]))],
  ];
  for (const [kind, merged] of cases) {
    const result = checkParity(listed, merged, FILES, false);
    assert.equal(result.ok, false, kind);
    assert.equal(
      result.problems[kind].length,
      1,
      `${kind}: ${JSON.stringify(result.problems)}`,
    );
  }
  // A spec file Playwright lists but the shard plan does not divide.
  const unplanned = checkParity(listed, report(one, two, three), [
    "flows/a.spec.ts",
  ]);
  assert.equal(unplanned.ok, false);
  assert.deepEqual(unplanned.problems.unplanned, ["flows/b.spec.ts"]);
  // An empty listing proves nothing.
  assert.equal(checkParity(report(), report(), FILES).ok, false);
});

// --- workflow wiring ---------------------------------------------------------

const workflow = () =>
  parseYaml(
    readFileSync(
      new URL("../../.github/workflows/e2e-ci.yml", import.meta.url),
      "utf8",
    ),
  );

test("e2e-ci.yml runs each shard's share, proves parity, and gates the merge on it", () => {
  const jobs = workflow().jobs;
  const step = (job, name) => job.steps.find((entry) => entry.name === name);
  assert.equal(
    step(jobs.e2e, "Run E2E suite").env.E2E_SHARD,
    "${{ matrix.shard }}/${{ needs.changes.outputs.shard_total }}",
  );
  assert.equal(
    jobs.e2e.strategy.matrix.shard,
    "${{ fromJSON(needs.changes.outputs.shards) }}",
  );
  assert.equal(jobs.e2e.strategy["fail-fast"], false);
  // Once per stack: on the last shard, never on none.
  assert.equal(
    jobs.e2e.env.E2E_LAST_SHARD,
    "${{ matrix.shard == needs.changes.outputs.shard_total }}",
  );
  assert.equal(
    step(jobs.e2e, "Flow catalog is current").if,
    "env.E2E_LAST_SHARD == 'true'",
  );
  assert.match(
    step(jobs.e2e, "Resolve the futureagi-sdk ref").if,
    /env\.E2E_LAST_SHARD == 'true'/,
  );
  // The parity proof cannot be skipped or softened, and the gate waits for it.
  const parity = step(jobs.report, "The shards ran every test exactly once");
  assert.equal(parity.if, undefined);
  assert.match(parity.run, /shard-report\.mjs parity/);
  assert.match(parity.run, /--phase-runtime-skips --out shard-parity\.json/);
  assert.equal(parity.env.E2E_ENFORCE_RUNTIME_SKIPS, "${{ vars.E2E_ENFORCE_RUNTIME_SKIPS }}");
  assert.ok(jobs.report.steps.some((entry) => entry.uses?.startsWith("actions/upload-artifact@") &&
    entry.with.path.split("\n").includes("e2e/shard-parity.json")));
  assert.ok(jobs.report.steps.every((entry) => !entry["continue-on-error"]));
  assert.equal(
    jobs.report.if,
    "${{ !cancelled() && needs.changes.outputs.any == 'true' }}",
  );
  assert.deepEqual(jobs["e2e-tests"].needs, ["changes", "e2e", "report"]);
  const metadata = "github.event.action == 'edited' && !github.event.changes.base && (github.event.changes.body || github.event.changes.title)";
  assert.equal(jobs["e2e-tests"].name, `\${{ ${metadata} && 'E2E metadata edit' || 'E2E Tests Pass' }}`);
  assert.equal(jobs["e2e-tests"].if, `\${{ always() && !(${metadata}) }}`);
});

test("the E2E gate passes only on a green suite with its report, or when nothing was needed", () => {
  const gate = workflow().jobs["e2e-tests"].steps[0];
  assert.deepEqual(gate.env, {
    CHANGES: "${{ needs.changes.result }}",
    E2E_NEEDED: "${{ needs.changes.outputs.any }}",
    E2E: "${{ needs.e2e.result }}",
    REPORT: "${{ needs.report.result }}",
  });
  const cases = [
    // changes, e2e needed, e2e, report, passes
    ["success", "true", "success", "success", true],
    ["success", "false", "skipped", "skipped", true],
    // A skipped report job is a suite without its parity proof.
    ["success", "true", "success", "skipped", false],
    ["success", "true", "success", "failure", false],
    ["success", "true", "success", "cancelled", false],
    ["success", "true", "failure", "success", false],
    ["success", "true", "cancelled", "skipped", false],
    ["success", "true", "skipped", "skipped", false],
    ["success", "false", "success", "success", false],
    ["success", "", "skipped", "skipped", false],
    ["failure", "", "skipped", "skipped", false],
    ["cancelled", "true", "success", "success", false],
    ["skipped", "false", "skipped", "skipped", false],
  ];
  for (const [CHANGES, E2E_NEEDED, E2E, REPORT, passes] of cases) {
    // The shell that runs a step's script.
    const run = spawnSync(
      "bash",
      ["--noprofile", "--norc", "-eo", "pipefail", "-c", gate.run],
      {
        encoding: "utf8",
        env: { PATH: process.env.PATH, CHANGES, E2E_NEEDED, E2E, REPORT },
      },
    );
    assert.equal(
      run.status === 0,
      passes,
      `${CHANGES}/${E2E_NEEDED}/${E2E}/${REPORT}: ${run.stdout}`,
    );
  }
});

test("the report job requires proof that the once-per-stack steps ran and passed", () => {
  const jobs = workflow().jobs;
  const step = (job, name) => job.steps.find((entry) => entry.name === name);
  // The record names the three steps by their real outcomes.
  const record = step(jobs.e2e, "Record the once-per-stack steps");
  assert.equal(
    record.if,
    "${{ !cancelled() && env.E2E_LAST_SHARD == 'true' }}",
  );
  assert.deepEqual(record.env, {
    CATALOG: "${{ steps.catalog.outcome }}",
    SDK_PYTHON: "${{ steps.sdk-python.outcome }}",
    SDK_TYPESCRIPT: "${{ steps.sdk-typescript.outcome }}",
    SDK_SHA: "${{ steps.sdk-ref.outputs.ref }}",
    TOTAL: "${{ needs.changes.outputs.shard_total }}",
  });
  assert.equal(step(jobs.e2e, "Flow catalog is current").id, "catalog");
  assert.equal(step(jobs.e2e, "Python SDK live suite").id, "sdk-python");
  assert.equal(
    step(jobs.e2e, "TypeScript SDK live suite").id,
    "sdk-typescript",
  );
  const upload = step(jobs.e2e, "Upload the once-per-stack record");
  assert.equal(upload.with.name, "once-per-stack-${{ matrix.stack }}-attempt-${{ github.run_attempt }}");
  assert.equal(upload.with["if-no-files-found"], "error");
  // A missing record fails the download; a record with a skip fails the check.
  const download = step(jobs.report, "Download verified shard and once-per-stack receipts");
  assert.equal(download.run, "node scripts/shard-report.mjs receipts");
  assert.equal(download.if, undefined);
  const check = step(
    jobs.report,
    "The flow catalog check and the SDK suites ran and passed",
  );
  assert.equal(check.if, undefined);
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-once-test-"));
  try {
    mkdirSync(path.join(dir, "once-per-stack"));
    const outcomes = (catalog, python, typescript) => ({
      flow_catalog_check: catalog,
      python_sdk_suite: python,
      typescript_sdk_suite: typescript,
    });
    for (const [content, passes] of [
      [outcomes("success", "success", "success"), true],
      [outcomes("success", "skipped", "success"), false],
      [outcomes("skipped", "skipped", "skipped"), false],
      [outcomes("success", "success", "failure"), false],
      [{ flow_catalog_check: "success" }, false],
      [{ a: "success", b: "success", c: "success" }, false],
      [{}, false],
    ]) {
      writeFileSync(
        path.join(dir, "once-per-stack", "record.json"),
        JSON.stringify({outcomes: content}),
      );
      const run = spawnSync(
        "bash",
        ["--noprofile", "--norc", "-eo", "pipefail", "-c", check.run],
        { cwd: dir, encoding: "utf8" },
      );
      assert.equal(run.status === 0, passes, JSON.stringify(content));
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("one run uses one date for the quarantine filter", () => {
  const jobs = workflow().jobs;
  for (const job of [jobs.e2e, jobs.report]) {
    assert.equal(
      job.env.E2E_QUARANTINE_TODAY,
      "${{ needs.changes.outputs.today }}",
    );
  }
  const saved = { ...process.env };
  try {
    delete process.env.E2E_INCLUDE_QUARANTINED;
    process.env.E2E_LIVE_LLM = "1";
    process.env.E2E_QUARANTINE_TODAY = "2026-11-21";
    // The committed file decides the pattern; with no entry there is none.
    const pattern = grepInvertPattern();
    assert.ok(pattern === undefined || pattern instanceof RegExp);
    process.env.E2E_QUARANTINE_TODAY = "tomorrow";
    assert.throws(() => grepInvertPattern(), /E2E_QUARANTINE_TODAY/);
  } finally {
    process.env = saved;
  }
});
