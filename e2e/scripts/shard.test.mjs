import assert from "node:assert/strict";
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
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
import { checkParity, fileDurations } from "./shard-report.mjs";

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
    const result = checkParity(listed, merged, FILES);
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
