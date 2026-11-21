#!/usr/bin/env node
// Two jobs over Playwright JSON reports, for the sharded suite (lib/shard.ts):
//
//   parity     proves the shards together covered the whole suite: every test
//              that an unsharded `playwright test --list` names has exactly one
//              result in the merged report, the merged report has no test the
//              listing does not name, and that one result is a run of the
//              test's body. A skip counts only when the listing already
//              declares it (an opt-in or stack-only test) or when
//              .runtime-skips.json allows it for this stack. CI fails on any
//              other difference.
//   durations  rewrites .test-durations.json (seconds of test time per spec
//              file) from a merged report, to rebalance the shards.
//
//   node scripts/shard-report.mjs parity --listed list.json --merged merged.json
//   node scripts/shard-report.mjs durations --merged merged.json
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { listSpecFiles } from "../lib/shard.ts";

const E2E_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_SKIPS = path.join(E2E_DIR, ".runtime-skips.json");
const STACKS = ["distributed", "standalone"];
const SKIP_ANNOTATIONS = new Set(["skip", "fixme"]);

/**
 * Every test of a JSON report (a listing or a run), flattened: one entry per
 * spec and project. The key is Playwright's own test id, which does not move
 * when a line is added above the test; a report without ids falls back to the
 * location.
 */
export function collectSpecs(report) {
  const specs = [];
  const walk = (suite) => {
    for (const spec of suite.specs ?? []) {
      const label = `${spec.file}:${spec.line} ${spec.title}`;
      const identity =
        spec.id ?? `${spec.file}:${spec.line}:${spec.column} ${spec.title}`;
      const tests = spec.tests?.length ? spec.tests : [{}];
      for (const test of tests) {
        const results = test.results ?? [];
        specs.push({
          key: `${identity} [${test.projectName ?? ""}]`,
          label,
          title: spec.title,
          file: spec.file,
          results: results.length,
          // A result with status "skipped" is a test that did not run its body.
          executed: results.filter((result) => result.status !== "skipped")
            .length,
          declaredSkip:
            test.expectedStatus === "skipped" ||
            (test.annotations ?? []).some((note) =>
              SKIP_ANNOTATIONS.has(note.type),
            ),
          seconds:
            results.reduce((sum, result) => sum + (result.duration ?? 0), 0) /
            1000,
        });
      }
    }
    for (const child of suite.suites ?? []) walk(child);
  };
  for (const suite of report.suites ?? []) walk(suite);
  return specs;
}

/**
 * The tests that may skip themselves while they run (.runtime-skips.json).
 * Unlike the quarantine list this one fails closed: a broken entry stops the
 * gate, because an entry here is a test that proves nothing on that stack.
 */
export function readRuntimeSkips(file = RUNTIME_SKIPS) {
  const entries = JSON.parse(readFileSync(file, "utf8"));
  if (!Array.isArray(entries)) throw new Error(`${file}: expected a list`);
  for (const entry of entries) {
    const bad = ["id", "stack", "reason"].filter(
      (field) => typeof entry?.[field] !== "string" || !entry[field].trim(),
    );
    if (bad.length || !STACKS.includes(entry.stack))
      throw new Error(
        `${file}: each entry needs id, reason and stack (${STACKS.join(" or ")}): ${JSON.stringify(entry)}`,
      );
  }
  return entries;
}

/**
 * Compares the unsharded listing with the merged run. `specFiles` is what the
 * shard plan divides (lib/shard.ts walks the spec roots); a listed file outside
 * it belongs to no shard. `allowedSkips` are the ids (title substrings) of
 * .runtime-skips.json for the stack of this run.
 */
export function checkParity(listed, merged, specFiles, allowedSkips = []) {
  const expected = collectSpecs(listed);
  const actual = collectSpecs(merged);
  const expectedByKey = new Map(expected.map((spec) => [spec.key, spec]));
  const seen = new Map();
  for (const spec of actual)
    seen.set(spec.key, [...(seen.get(spec.key) ?? []), spec]);
  const planned = new Set(specFiles);
  const allowed = (spec) => allowedSkips.some((id) => spec.title.includes(id));
  // Reported once, started, and every result is a skip.
  const skipped = [...seen].filter(
    ([, entries]) =>
      entries.length === 1 && entries[0].results > 0 && !entries[0].executed,
  );
  const declared = skipped.filter(
    ([key]) => expectedByKey.get(key)?.declaredSkip,
  );
  const atRunTime = skipped.filter(
    ([key]) => expectedByKey.has(key) && !expectedByKey.get(key).declaredSkip,
  );
  const labels = (pairs) => pairs.map(([, [spec]]) => spec.label).sort();
  const label = (key) => (expectedByKey.get(key) ?? seen.get(key)[0]).label;
  const problems = {
    // A spec file Playwright runs but the shard plan never hands to a shard.
    unplanned: [...new Set(expected.map((spec) => spec.file))]
      .filter((file) => !planned.has(file))
      .sort(),
    // Listed, absent from every shard's report.
    missing: [...expectedByKey.keys()]
      .filter((key) => !seen.has(key))
      .map(label)
      .sort(),
    // Reported by a shard but not in the listing.
    extra: [...seen.keys()]
      .filter((key) => !expectedByKey.has(key))
      .map(label)
      .sort(),
    // Listed and reported, but it never started: a shard stopped early, or
    // an earlier test of a serial group failed (the real failure is then in
    // that shard's own log).
    notRun: [...seen]
      .filter(([, entries]) => entries.every((spec) => spec.results === 0))
      .map(([key]) => label(key))
      .sort(),
    // Ran in more than one shard. Retries are off, so one test has one result.
    duplicated: [...seen]
      .filter(
        ([, entries]) =>
          entries.length > 1 || entries.some((spec) => spec.results > 1),
      )
      .map(([key]) => label(key))
      .sort(),
    // The test skipped itself while it ran, the listing does not declare the
    // skip, and .runtime-skips.json does not allow it: a green run would
    // count a body that never ran.
    skippedAtRunTime: labels(atRunTime.filter(([, [spec]]) => !allowed(spec))),
    listedTwice: expected.length - expectedByKey.size,
  };
  const ok =
    !problems.unplanned.length &&
    !problems.missing.length &&
    !problems.extra.length &&
    !problems.notRun.length &&
    !problems.duplicated.length &&
    !problems.skippedAtRunTime.length &&
    !problems.listedTwice &&
    expected.length > 0;
  return {
    ok,
    listed: expected.length,
    reported: actual.length,
    files: new Set(expected.map((spec) => spec.file)).size,
    // What the one result of each test was.
    executed: [...seen.values()].filter(
      (entries) => entries.length === 1 && entries[0].executed === 1,
    ).length,
    declaredSkips: labels(declared),
    allowedRuntimeSkips: labels(
      atRunTime.filter(([, [spec]]) => allowed(spec)),
    ),
    // An allowance for a test that ran its body: the entry is out of date.
    staleAllowances: allowedSkips
      .filter((id) =>
        actual.some((spec) => spec.title.includes(id) && spec.executed),
      )
      .sort(),
    problems,
  };
}

/** Seconds of test time per spec file, keys sorted, one decimal. */
export function fileDurations(merged) {
  const totals = new Map();
  for (const spec of collectSpecs(merged)) {
    if (spec.results)
      totals.set(spec.file, (totals.get(spec.file) ?? 0) + spec.seconds);
  }
  return Object.fromEntries(
    [...totals]
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([file, seconds]) => [file, Math.round(seconds * 10) / 10]),
  );
}

function option(args, name) {
  const at = args.indexOf(name);
  if (at === -1 || !args[at + 1]) throw new Error(`missing ${name} <file>`);
  return args[at + 1];
}

function main([command, ...args]) {
  const read = (name) => JSON.parse(readFileSync(option(args, name), "utf8"));
  if (command === "parity") {
    const stack =
      process.env.E2E_STACK === "standalone" ? "standalone" : "distributed";
    const result = checkParity(
      read("--listed"),
      read("--merged"),
      listSpecFiles(),
      readRuntimeSkips()
        .filter((entry) => entry.stack === stack)
        .map((entry) => entry.id),
    );
    console.log(
      `listed ${result.listed} tests in ${result.files} spec files; the shards reported ${result.reported}`,
    );
    console.log(
      `${result.executed} ran their body; ${result.declaredSkips.length} are skipped in the listing; ` +
        `${result.allowedRuntimeSkips.length} skipped themselves at run time and .runtime-skips.json allows it (${stack})`,
    );
    for (const name of result.declaredSkips)
      console.log(`  skipped in the listing: ${name}`);
    for (const name of result.allowedRuntimeSkips)
      console.log(
        `::warning title=E2E test did not run its body::${name} skipped itself at run time (.runtime-skips.json)`,
      );
    for (const id of result.staleAllowances)
      console.log(
        `::warning title=E2E run-time skip list is out of date::${id} ran its body on ${stack}; remove its entry from e2e/.runtime-skips.json`,
      );
    for (const [kind, keys] of Object.entries(result.problems)) {
      if (Array.isArray(keys) && keys.length)
        console.log(
          `::error title=E2E shard parity: ${kind}::${keys.slice(0, 20).join(" | ")}`,
        );
    }
    if (result.problems.listedTwice)
      console.log(
        `::error title=E2E shard parity::${result.problems.listedTwice} listed tests share an id`,
      );
    if (!result.ok) {
      console.log(
        "The shards did not run exactly the unsharded suite, or a test skipped itself without an entry in e2e/.runtime-skips.json.",
      );
      return 1;
    }
    console.log(
      "Every listed test has exactly one result, and each result is a run of the body or a skip that is declared or allowed.",
    );
    return 0;
  }
  if (command === "durations") {
    const out = args.includes("--out")
      ? option(args, "--out")
      : path.join(E2E_DIR, ".test-durations.json");
    const durations = fileDurations(read("--merged"));
    writeFileSync(out, `${JSON.stringify(durations, null, 2)}\n`);
    console.log(
      `wrote ${Object.keys(durations).length} spec file durations to ${out}`,
    );
    return 0;
  }
  console.error(
    "usage: shard-report.mjs parity --listed <json> --merged <json> | durations --merged <json> [--out <file>]",
  );
  return 2;
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  process.exitCode = main(process.argv.slice(2));
}
