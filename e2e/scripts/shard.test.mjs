import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
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
import {
  annotation,
  checkParity,
  fileDurations,
  checkStackImages,
  readRuntimeSkips,
  receiptArtifact,
  runDate,
  shardAttempts,
} from "./shard-report.mjs";

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

test("each shard's receipts come from the attempt that ran it", () => {
  // A copy of an unre-run shard keeps the original start and end times.
  const job = (i, attempt, ran = attempt) => ({
    name: `e2e (distributed, ${i}/3)`,
    run_attempt: attempt,
    started_at: `2026-11-22T0${ran}:00:00Z`,
    completed_at: `2026-11-22T0${ran}:30:00Z`,
  });
  const first = [job(1, 1), job(2, 1), job(3, 1)];
  assert.deepEqual(shardAttempts("distributed", 3, first), [1, 1, 1]);
  // Re-run failed jobs re-ran only shard 2: its receipts are from attempt 2,
  // the others' from attempt 1 (as on run 45013774687).
  const rerun = [...first, job(1, 2, 1), job(2, 2), job(3, 2, 1)];
  assert.deepEqual(shardAttempts("distributed", 3, rerun), [1, 2, 1]);
  // A third attempt that re-ran only the report job copies all of them.
  const again = [...rerun, job(1, 3, 1), job(2, 3, 2), job(3, 3, 1)];
  assert.deepEqual(shardAttempts("distributed", 3, again), [1, 2, 1]);
  // Re-run all jobs: every shard ran at attempt 2.
  const all = [...first, job(1, 2), job(2, 2), job(3, 2)];
  assert.deepEqual(shardAttempts("distributed", 3, all), [2, 2, 2]);
  // Fails closed.
  assert.throws(
    () => shardAttempts("distributed", 3, first.slice(0, 2)),
    /missing/,
  );
  const unfinished = { ...job(2, 2), completed_at: null };
  assert.throws(
    () => shardAttempts("distributed", 3, [...first, unfinished]),
    /missing/,
  );
  assert.throws(
    () => shardAttempts("distributed", 3, [...first, job(2, 1)]),
    /repeated/,
  );
  // A copy at attempt 3 of a job last run at attempt 1, with no attempt-2 copy.
  assert.throws(
    () => shardAttempts("distributed", 3, [...first, job(2, 3, 1)]),
    /inconsistent/,
  );
});

test("a receipt is exactly one unexpired artifact", () => {
  const name = "blob-report-distributed-2-attempt-2";
  const row = { name, id: 1, digest: "sha256:x", expired: false };
  assert.equal(receiptArtifact([row], name), row);
  assert.throws(() => receiptArtifact([], name), /missing or repeated/);
  assert.throws(
    () => receiptArtifact([row, { ...row, id: 2 }], name),
    /missing or repeated/,
  );
  assert.throws(
    () => receiptArtifact([{ ...row, expired: true }], name),
    /missing or repeated/,
  );
  // A receipt of another attempt does not stand in for this one.
  assert.throws(
    () =>
      receiptArtifact(
        [{ ...row, name: "blob-report-distributed-2-attempt-1" }],
        name,
      ),
    /missing or repeated/,
  );
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
    assert.deepEqual(result.problems.runtimeSkipped, [
      JSON.stringify([FILES[0], 1, 1, "one", "chromium"]),
    ]);
    assert.deepEqual(result.problems.wrongOutcome, []);
  }
  // Other outcomes remain blocking, even with the runtime-skip gate off.
  for (const status of ["failed", "timedOut", "interrupted"]) {
    actual.tests[0].results[0].status = status;
    assert.equal(
      checkParity(report(plan), report(actual), FILES, false).ok,
      false,
    );
  }
  actual.tests[0].results[0].status = "skipped";
  plan.tests[0].annotations = [{ type: "fail" }];
  assert.equal(
    checkParity(report(plan), report(actual), FILES, false).ok,
    false,
  );
  plan.tests[0].annotations = [
    { type: "skip", description: "not in this topology" },
  ];
  const declared = checkParity(report(plan), report(actual), FILES);
  assert.equal(declared.ok, true);
  assert.deepEqual(declared.problems.runtimeSkipped, []);
  actual.tests[0].projectId = "different";
  assert.equal(
    checkParity(report(plan), report(actual), FILES, false).ok,
    false,
  );
});

test("with enforcement on, only a run-time skip listed for the stack passes", () => {
  // The catalogue id comes from the listing's flow annotation.
  const flow = (entry, id) => {
    entry.tests[0].annotations = [
      { type: "flow", description: JSON.stringify({ id }) },
    ];
    return entry;
  };
  // A test listed to pass whose one result is a skip: it skipped itself.
  const selfSkip = (title) => {
    const entry = spec("flows/a.spec.ts", 9, title, [0]);
    entry.tests[0].results[0].status = "skipped";
    return entry;
  };
  const listedTwo = report(
    spec("flows/a.spec.ts", 3, "one", []),
    flow(spec("flows/a.spec.ts", 9, "OBS-E2E-031: churn", []), "OBS-E2E-031"),
  );
  const merged = report(
    spec("flows/a.spec.ts", 3, "one", [1]),
    selfSkip("OBS-E2E-031: churn"),
  );
  const unlisted = checkParity(listedTwo, merged, FILES, true, []);
  assert.equal(unlisted.ok, false);
  assert.equal(unlisted.problems.runtimeSkipped.length, 1);
  const allowed = checkParity(listedTwo, merged, FILES, true, ["OBS-E2E-031"]);
  assert.equal(allowed.ok, true);
  assert.deepEqual(allowed.problems.runtimeSkipped, []);
  assert.equal(allowed.allowedRuntimeSkips.length, 1);
  assert.deepEqual(allowed.staleAllowances, []);
  // An allowance for another test does not cover this one, nor does a
  // prefix of the id or an id that only appears in the title.
  for (const other of ["OBS-E2E-032", "OBS-E2E-03", "E2E-031"])
    assert.equal(
      checkParity(listedTwo, merged, FILES, true, [other]).ok,
      false,
      other,
    );
  const titleOnly = report(
    spec("flows/a.spec.ts", 3, "one", []),
    spec("flows/a.spec.ts", 9, "OBS-E2E-031: churn", []),
  );
  assert.equal(
    checkParity(titleOnly, merged, FILES, true, ["OBS-E2E-031"]).ok,
    false,
  );
  // An allowance for a test that ran its body is reported as out of date.
  const ran = report(
    spec("flows/a.spec.ts", 3, "one", [1]),
    spec("flows/a.spec.ts", 9, "OBS-E2E-031: churn", [1]),
  );
  const stale = checkParity(listedTwo, ran, FILES, true, ["OBS-E2E-031"]);
  assert.equal(stale.ok, true);
  assert.deepEqual(stale.staleAllowances, ["OBS-E2E-031"]);
  // A test that failed, or never ran, does not make its entry stale.
  const failed = spec("flows/a.spec.ts", 9, "OBS-E2E-031: churn", [1]);
  failed.tests[0].results[0].status = "failed";
  const broken = checkParity(
    listedTwo,
    report(spec("flows/a.spec.ts", 3, "one", [1]), failed),
    FILES,
    true,
    ["OBS-E2E-031"],
  );
  assert.equal(broken.ok, false);
  assert.deepEqual(broken.staleAllowances, []);
  const absent = checkParity(
    listedTwo,
    report(spec("flows/a.spec.ts", 3, "one", [1])),
    FILES,
    true,
    ["OBS-E2E-031"],
  );
  assert.deepEqual(absent.staleAllowances, []);
});

test("annotation text escapes percent signs and line breaks", () => {
  assert.equal(annotation("100% a\r\nb"), "100%25 a%0D%0Ab");
});

test("the run-time skip list is strict, and the committed one is what CI measured", (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-skip-list-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = path.join(dir, "skips.json");
  const good = {
    id: "X-E2E-001",
    stack: "standalone",
    reason: "Distributed-only by design: no bind",
  };
  const today = new Date("2026-11-22T12:00:00Z");
  const temporary = {
    ...good,
    reason: "flaky guard",
    issue: "TH-1",
    owner: "observe flows",
    expires: "2027-01-06",
  };
  for (const bad of [
    "{",
    "{}",
    JSON.stringify([{ ...good, reason: " " }]),
    JSON.stringify([{ ...good, stack: "both" }]),
    JSON.stringify([{ stack: "standalone", reason: "why" }]),
    // Not by design, so it must name the ticket that removes it.
    JSON.stringify([{ ...good, reason: "flaky guard" }]),
    // A title, not a flow id.
    JSON.stringify([{ ...good, id: "managed background routing" }]),
    JSON.stringify([{ ...temporary, issue: " " }]),
    // A temporary entry names an owner and an expiry at most 45 days ahead.
    JSON.stringify([{ ...temporary, owner: undefined }]),
    JSON.stringify([{ ...temporary, expires: undefined }]),
    JSON.stringify([{ ...temporary, expires: "2027-01-07" }]),
    JSON.stringify([{ ...temporary, expires: "6 Jan" }]),
  ]) {
    writeFileSync(file, bad);
    assert.throws(() => readRuntimeSkips(file, today), bad);
  }
  writeFileSync(file, JSON.stringify([good, temporary]));
  const read = readRuntimeSkips(file, today);
  assert.equal(read.length, 2);
  assert.equal(read[1].expired, false);
  // After its expiry the entry is read but no longer allows a skip.
  assert.equal(
    readRuntimeSkips(file, new Date("2027-01-07T00:00:00Z"))[1].expired,
    true,
  );
  const committed = readRuntimeSkips();
  const by = (stack) =>
    committed
      .filter((entry) => entry.stack === stack)
      .map((entry) => entry.id)
      .sort();
  // Exact flow ids, ticketed, owned and expiring (TH-10597). The pager tests
  // ran and passed on Distributed (E2E CI run 45072697985), so they have no
  // entry there; Standalone keeps its entries until a Standalone run shows
  // the same. The two Distributed-only tests are discovery-time skips on
  // Standalone, so they need no entry.
  assert.deepEqual(by("distributed"), []);
  assert.deepEqual(by("standalone"), ["OBS-E2E-031", "OBS-E2E-032"]);
  for (const entry of committed) {
    assert.equal(entry.issue, "TH-10597");
    assert.ok(entry.owner.trim() && /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(entry.expires));
  }
  const names = committed.map((entry) => `${entry.stack} ${entry.id}`);
  assert.equal(new Set(names).size, names.length);
});

test("skip expiry is judged on the run date, not wall time", () => {
  assert.equal(
    runDate({ E2E_QUARANTINE_TODAY: "2027-01-06" }).toISOString(),
    "2027-01-06T00:00:00.000Z",
  );
  for (const bad of [undefined, "", "tomorrow", "2027-1-6"])
    assert.throws(() => runDate({ E2E_QUARANTINE_TODAY: bad }), /run date/);
  // The same entry, read on its last day and the day after.
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-run-date-"));
  try {
    const file = path.join(dir, "skips.json");
    writeFileSync(file, JSON.stringify([{ id: "OBS-E2E-031", stack: "distributed", issue: "TH-1",
      reason: "r", owner: "o", expires: "2027-01-06" }]));
    const on = (day) => readRuntimeSkips(file, runDate({ E2E_QUARANTINE_TODAY: day }))[0].expired;
    assert.equal(on("2027-01-06"), false);
    assert.equal(on("2027-01-07"), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the phased parity CLI reports the same runtime skips in warning and enforced modes", (t) => {
  const dir = mkdtempSync(path.join(tmpdir(), "e2e-runtime-skips-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = listSpecFiles()[0];
  const actual = spec(file, 1, "runtime skip", [0]);
  actual.tests[0].results[0].status = actual.tests[0].expectedStatus =
    "skipped";
  writeFileSync(
    path.join(dir, "listed.json"),
    JSON.stringify(report(spec(file, 1, "runtime skip", []))),
  );
  writeFileSync(path.join(dir, "merged.json"), JSON.stringify(report(actual)));
  for (const enforce of [false, true]) {
    const summary = path.join(dir, `summary-${enforce}`);
    const run = spawnSync(
      process.execPath,
      [
        fileURLToPath(new URL("./shard-report.mjs", import.meta.url)),
        "parity",
        "--phase-runtime-skips",
        "--listed",
        "listed.json",
        "--merged",
        "merged.json",
        "--out",
        "parity.json",
      ],
      {
        cwd: dir,
        encoding: "utf8",
        env: {
          ...process.env,
          GITHUB_STEP_SUMMARY: summary,
          E2E_QUARANTINE_TODAY: "2026-11-22",
          E2E_ENFORCE_RUNTIME_SKIPS: enforce ? "true" : "",
        },
      },
    );
    assert.equal(run.status, enforce ? 1 : 0, run.stderr);
    assert.match(
      run.stdout,
      enforce ? /::error .*runtimeSkipped/ : /::warning .*runtimeSkipped/,
    );
    const receipt = JSON.parse(
      readFileSync(path.join(dir, "parity.json"), "utf8"),
    );
    assert.equal(receipt.enforceRuntimeSkips, enforce);
    assert.equal(receipt.ok, !enforce);
    assert.deepEqual(receipt.problems.runtimeSkipped, [
      JSON.stringify([file, 1, 1, "runtime skip", ""]),
    ]);
    const text = readFileSync(summary, "utf8");
    assert.ok(text.includes(receipt.problems.runtimeSkipped[0]));
    assert.match(
      text,
      /skipped at runtime: \*\*1\*\* \(0 allowed by e2e\/\.runtime-skips\.json, 1 unexpected;/,
    );
  }
});

test("a test keeps its identity when its line moves, and loses it when its title changes", () => {
  // Playwright's test id is a hash of the file and the title path, not of the
  // position. A report without ids (the cases above) falls back to position.
  const withId = (line, title = "one") => ({
    ...spec("flows/a.spec.ts", line, title, [1]),
    id: `id-of-${title}`,
  });
  const listedWithIds = report({ ...withId(3), tests: [{ results: [] }] });
  assert.equal(checkParity(listedWithIds, report(withId(30)), FILES).ok, true);
  const renamed = checkParity(listedWithIds, report(withId(3, "ONE")), FILES);
  assert.equal(renamed.ok, false);
  assert.equal(renamed.problems.missing.length, 1);
  assert.equal(renamed.problems.extra.length, 1);
  // The id decides, not the title: the same file, line and title with
  // another Playwright id is another test.
  const sameTitle = report({ ...withId(3), id: "id-of-another-test" });
  const otherId = checkParity(listedWithIds, sameTitle, FILES);
  assert.equal(otherId.ok, false);
  assert.equal(otherId.problems.missing.length, 1);
  assert.equal(otherId.problems.extra.length, 1);
  // The key keeps the title in its fourth position for qualify-topology.mjs.
  assert.equal(JSON.parse(renamed.problems.extra[0])[3], "ONE");
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

const workflowText = () =>
  readFileSync(
    new URL("../../.github/workflows/e2e-ci.yml", import.meta.url),
    "utf8",
  );
const workflow = () => parseYaml(workflowText());

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
  assert.equal(
    parity.env.E2E_ENFORCE_RUNTIME_SKIPS,
    "${{ vars.E2E_ENFORCE_RUNTIME_SKIPS }}",
  );
  assert.ok(
    jobs.report.steps.some(
      (entry) =>
        entry.uses?.startsWith("actions/upload-artifact@") &&
        entry.with.path.split("\n").includes("e2e/shard-parity.json"),
    ),
  );
  assert.ok(jobs.report.steps.every((entry) => !entry["continue-on-error"]));
  assert.equal(
    jobs.report.if,
    "${{ !cancelled() && needs.changes.outputs.any == 'true' }}",
  );
  assert.deepEqual(jobs["e2e-tests"].needs, ["changes", "e2e", "report"]);
  // A title or body edit, or a label other than e2e-standalone, is metadata:
  // its run has no jobs and no "E2E Tests Pass" check.
  const metadata =
    "((github.event.action == 'edited' && !github.event.changes.base && (github.event.changes.body || github.event.changes.title)) || (github.event.action == 'labeled' && github.event.label.name != 'e2e-standalone'))";
  assert.equal(
    jobs["e2e-tests"].name,
    `\${{ ${metadata} && 'E2E metadata edit' || 'E2E Tests Pass' }}`,
  );
  assert.equal(jobs["e2e-tests"].if, `\${{ always() && !${metadata} }}`);
  assert.equal(jobs.changes.if, `\${{ !${metadata} }}`);
  assert.ok(
    workflowText().includes(`${metadata} && github.run_id || 'candidate'`),
  );
  assert.deepEqual(workflow().on.pull_request.types, [
    "opened",
    "synchronize",
    "reopened",
    "edited",
    "labeled",
  ]);
  // The same expressions, evaluated: only a real candidate event, or adding
  // e2e-standalone, gets jobs and the protected check name.
  const evaluate = (expression, event) =>
    new Function("github", `return (${expression});`)({ event });
  const name = jobs["e2e-tests"].name.slice(4, -3);
  for (const [event, real] of [
    [{ action: "opened", changes: {} }, true],
    [{ action: "synchronize", changes: {} }, true],
    [{ action: "edited", changes: { body: { from: "x" } } }, false],
    [{ action: "edited", changes: { title: { from: "x" } } }, false],
    [{ action: "edited", changes: { base: { ref: { from: "main" } }, body: {} } }, true],
    [{ action: "labeled", changes: {}, label: { name: "e2e-standalone" } }, true],
    [{ action: "labeled", changes: {}, label: { name: "general-factory" } }, false],
  ]) {
    assert.equal(!evaluate(metadata, event), real, JSON.stringify(event));
    assert.equal(
      evaluate(name, event),
      real ? "E2E Tests Pass" : "E2E metadata edit",
      JSON.stringify(event),
    );
  }
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
  // What a re-run of the report job downloads from the attempt that made it
  // must outlive the days in which a failed run is still re-run.
  for (const name of ["Upload blob report", "Upload the once-per-stack record"])
    assert.ok(step(jobs.e2e, name).with["retention-days"] >= 14, name);
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
  assert.equal(
    upload.with.name,
    "once-per-stack-${{ matrix.stack }}-attempt-${{ github.run_attempt }}",
  );
  assert.equal(upload.with["if-no-files-found"], "error");
  // A missing record fails the download; a record with a skip fails the check.
  const download = step(
    jobs.report,
    "Download verified shard and once-per-stack receipts",
  );
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
        JSON.stringify({ outcomes: content }),
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

// Trimmed from run 45034667773 (one attempt, six shards): each job's own
// build has its own digest; the pulled images were the same on every shard.
const BUILT_DIGESTS = [
  "futureagi/future-agi@sha256:c6788e915a5eb6e6f7b316b00a875314224374d8118a67a65ac9cec95e16310c",
  "futureagi/future-agi@sha256:ed3f275ec2c5318327acdbab48e3ddb043daf72c325d3e99612d26b39c024021",
  "futureagi/future-agi@sha256:5ce2bddd0adf0916d07d90e3472b146e1cf94de6e7ae0abd4e24c49616d92b40",
];
const PULLED_LATEST = "futureagi/future-agi@sha256:5299263cf2029419b301d6fe7dd401e5d21bdc3011da832928ccb57dea9f99fe";
const PULLED_POSTGRES = "postgres@sha256:3950ad7c3148ca2175ef1b6c36c9efbf78957ae8fbe8b3a5d7c35af50500382f";
const stackRecord = (built, postgres = PULLED_POSTGRES) => [
  { ref: "futureagi/future-agi:e2e-ci", built: true, digests: [built] },
  { ref: "futureagi/future-agi:latest", built: false, digests: [PULLED_LATEST] },
  { ref: "postgres:16", built: false, digests: [postgres] },
];

test("shards from different attempts join one report only with the same pulled images", () => {
  const moved = "postgres@sha256:" + "b".repeat(64);
  const [a, b, c] = BUILT_DIGESTS;
  // The real one-attempt run: only the job-built images differ, and they are
  // never compared, on one attempt or across attempts.
  checkStackImages([stackRecord(a), stackRecord(b), stackRecord(c)], [1, 1, 1]);
  checkStackImages([stackRecord(a), stackRecord(b), stackRecord(c)], [2, 1, 2]);
  // Shards of one attempt ran together: a mutable tag that moved between two
  // of them does not fail the run (as before re-run shards could join).
  checkStackImages([stackRecord(a), stackRecord(b, moved), stackRecord(c)], [1, 1, 1]);
  assert.throws(
    () => checkStackImages([stackRecord(a), stackRecord(b, moved), stackRecord(c)], [1, 2, 1]),
    /shards ran different pulled images \(postgres:16\); rerun all jobs/,
  );
  // A pulled image only one shard ran is drift too.
  const redis = { ref: "redis:7", built: false, digests: ["redis@sha256:c"] };
  assert.throws(
    () => checkStackImages([stackRecord(a), [...stackRecord(b), redis]], [2, 1]),
    /\(redis:7\)/,
  );
  // Every record is validated on every run, including the built marker.
  for (const bad of [
    [],
    [{ ref: 1, built: false, digests: [] }],
    [{ ref: "x", built: false }],
    [{ ref: "x", built: false, digests: [1] }],
    [{ ref: "x", digests: [] }],
    [{ ref: "x", built: "true", digests: [] }],
  ])
    assert.throws(() => checkStackImages([bad], [1]), /invalid stack image record/);
  for (const attempts of [[], [0], [1, 1], ["1"]])
    assert.throws(() => checkStackImages([stackRecord(a)], attempts), /invalid shard attempts/);
});

test("receipts take each shard from the attempt that ran it and the stack record from the last shard's", () => {
  // Drives `shard-report.mjs receipts` against fake gh, unzip and sha256sum.
  const root = mkdtempSync(path.join(tmpdir(), "e2e-receipts-test-"));
  const tool = (name, body) =>
    writeFileSync(path.join(root, "bin", name), `#!/usr/bin/env node\n${body}\n`, { mode: 0o755 });
  mkdirSync(path.join(root, "bin"));
  tool(
    "gh",
    `const fs = require("node:fs");
const url = process.argv[process.argv.length - 1];
const store = process.env.FAKE_STORE;
const zip = /\\/artifacts\\/(\\d+)\\/zip$/.exec(url);
if (zip) process.stdout.write(fs.readFileSync(store + "/" + zip[1]));
else if (url.includes("/jobs?")) process.stdout.write(fs.readFileSync(store + "/jobs.json"));
else if (url.includes("/artifacts?")) process.stdout.write(fs.readFileSync(store + "/artifacts.json"));
else process.exit(3);`,
  );
  tool(
    "unzip",
    `const fs = require("node:fs");
const [flag, file, member] = process.argv.slice(2);
const archive = JSON.parse(fs.readFileSync(file, "utf8"));
if (flag === "-Z1") process.stdout.write(archive.member + "\\n");
else if (flag === "-p" && member === archive.member) process.stdout.write(archive.content);
else process.exit(9);`,
  );
  tool(
    "sha256sum",
    `const file = process.argv[2];
const hash = require("node:crypto").createHash("sha256").update(require("node:fs").readFileSync(file)).digest("hex");
process.stdout.write(hash + "  " + file + "\\n");`,
  );
  const job = (i, attempt, ran = attempt) => ({
    name: `e2e (distributed, ${i}/3)`,
    run_attempt: attempt,
    started_at: `2026-11-22T0${ran}:00:00Z`,
    completed_at: `2026-11-22T0${ran}:30:00Z`,
  });
  // `ran` lists, per attempt, the shards that ran in it; `images` gives a
  // shard's record for the attempt that ran it.
  const receipts = (name, ran, images) => {
    const dir = path.join(root, name);
    const store = path.join(dir, "store");
    const work = path.join(dir, "work");
    mkdirSync(store, { recursive: true });
    mkdirSync(work);
    const jobs = [];
    const artifacts = [];
    const put = (artifact, member, content) => {
      const bytes = Buffer.from(JSON.stringify({ member, content }));
      const id = 100 + artifacts.length;
      writeFileSync(path.join(store, String(id)), bytes);
      artifacts.push({
        id,
        name: artifact,
        expired: false,
        digest: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
      });
    };
    const last = [];
    ran.forEach((shards, index) => {
      const attempt = index + 1;
      for (let i = 1; i <= 3; i++) {
        if (shards.includes(i)) {
          last[i] = attempt;
          jobs.push(job(i, attempt));
          put(`blob-report-distributed-${i}-attempt-${attempt}`, `report-${i}-of-3.zip`, `blob ${i} attempt ${attempt}`);
          put(`stack-images-distributed-${i}-attempt-${attempt}`, "images.json", JSON.stringify(images(i, attempt)));
          if (i === 3)
            put(`once-per-stack-distributed-attempt-${attempt}`, "record.json", JSON.stringify({
              run: "77", sha: "a".repeat(40), stack: "distributed", total: 3,
              attempt, date: "2026-11-22", sdk: "b".repeat(40),
            }));
        } else jobs.push(job(i, attempt, last[i]));
      }
    });
    writeFileSync(path.join(store, "jobs.json"), JSON.stringify([{ jobs }]));
    writeFileSync(path.join(store, "artifacts.json"), JSON.stringify([{ artifacts }]));
    const run = spawnSync(
      process.execPath,
      [fileURLToPath(new URL("./shard-report.mjs", import.meta.url)), "receipts"],
      {
        cwd: work,
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${path.join(root, "bin")}${path.delimiter}${process.env.PATH}`,
          FAKE_STORE: store,
          GITHUB_REPOSITORY: "owner/repo",
          GITHUB_RUN_ID: "77",
          GITHUB_SHA: "a".repeat(40),
          E2E_STACK: "distributed",
          SHARD_TOTAL: "3",
          E2E_QUARANTINE_TODAY: "2026-11-22",
        },
      },
    );
    const read = (file) => readFileSync(path.join(work, file), "utf8");
    return { ...run, read };
  };
  const moved = "postgres@sha256:" + "b".repeat(64);
  const own = (i, attempt) => stackRecord(BUILT_DIGESTS[(i + attempt) % 3]);
  try {
    // Re-run failed jobs re-ran shard 1 and the last shard; shard 2 is carried
    // over from attempt 1. Each job rebuilt its images; the pulls match.
    const mixed = receipts("mixed", [[1, 2, 3], [1, 3]], own);
    assert.equal(mixed.status, 0, mixed.stderr);
    assert.equal(mixed.read("all-blob-reports/report-1-of-3.zip"), "blob 1 attempt 2");
    assert.equal(mixed.read("all-blob-reports/report-2-of-3.zip"), "blob 2 attempt 1");
    assert.equal(mixed.read("all-blob-reports/report-3-of-3.zip"), "blob 3 attempt 2");
    assert.equal(JSON.parse(mixed.read("once-per-stack/record.json")).attempt, 2);
    assert.match(mixed.stdout, /stack-images-distributed-2-attempt-1: attempt 1/);
    assert.match(mixed.stdout, /once-per-stack-distributed-attempt-2: attempt 2/);
    // The same re-run after a mutable tag moved: the report says to rerun all.
    const drift = receipts("drift", [[1, 2, 3], [1, 3]], (i, attempt) =>
      attempt === 2 ? stackRecord(BUILT_DIGESTS[i % 3], moved) : own(i, attempt),
    );
    assert.notEqual(drift.status, 0);
    assert.match(drift.stderr, /shards ran different pulled images \(postgres:16\); rerun all jobs/);
    // One attempt: the tag moved between two shards, and the run still passes.
    const single = receipts("single", [[1, 2, 3]], (i) =>
      i === 2 ? stackRecord(BUILT_DIGESTS[1], moved) : own(i, 1),
    );
    assert.equal(single.status, 0, single.stderr);
    assert.equal(JSON.parse(single.read("once-per-stack/record.json")).attempt, 1);
    // A record without the built marker fails even on one attempt.
    const unmarked = receipts("unmarked", [[1, 2, 3]], (i) =>
      own(i, 1).map(({ built, ...row }) => (i === 3 ? row : { built, ...row })),
    );
    assert.notEqual(unmarked.status, 0);
    assert.match(unmarked.stderr, /invalid stack image record for shard 3/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("both workflows record and upload each shard's pulled images under one name", () => {
  const steps = (file) => {
    const text = readFileSync(new URL(`../../.github/workflows/${file}`, import.meta.url), "utf8");
    const start = text.indexOf("      - name: Record the stack's pulled images\n");
    const end = text.indexOf("      - name: Upload blob report\n");
    assert.ok(start > 0 && end > start, file);
    return text.slice(start, end);
  };
  const ci = steps("e2e-ci.yml");
  assert.equal(steps("e2e-stack-profile.yml"), ci);
  assert.ok(ci.includes("name: stack-images-${{ matrix.stack }}-${{ matrix.shard }}-attempt-${{ github.run_attempt }}"));
  const report = readFileSync(new URL("./shard-report.mjs", import.meta.url), "utf8");
  assert.ok(report.includes("`stack-images-${stack}-${i}-attempt-${attempts[i - 1]}`"));
  assert.ok(report.includes("checkStackImages(images, attempts);"));
  // Every image a build step tags is marked built in the record.
  for (const file of ["e2e-ci.yml", "e2e-stack-profile.yml"]) {
    const text = readFileSync(new URL(`../../.github/workflows/${file}`, import.meta.url), "utf8");
    const tagged = new Set(
      [...text.matchAll(/(?:tags: |-t ")(futureagi\/[a-z-]+):(?:\$\{\{ env\.IMAGE_TAG \}\}|\$IMAGE_TAG)/g)].map((m) => m[1]),
    );
    assert.equal(tagged.size, 5, file);
    for (const repo of tagged) assert.ok(ci.includes(`"${repo}:$IMAGE_TAG"`), `${file}: ${repo}`);
  }
  assert.ok(ci.includes('if [[ $E2E_STACK == standalone ]]; then built+=("futureagi/standalone:$IMAGE_TAG"); fi'));
});

