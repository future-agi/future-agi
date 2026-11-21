#!/usr/bin/env node
// Two jobs over Playwright JSON reports, for the sharded suite (lib/shard.ts):
//
//   parity     proves the shards together ran the whole suite: every test that
//              an unsharded `playwright test --list` names has exactly one
//              result in the merged report, and the merged report has no test
//              the listing does not name. CI fails on any difference.
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

/** Every spec of a JSON report (a listing or a run), flattened. */
export function collectSpecs(report) {
  const specs = [];
  const walk = (suite) => {
    for (const spec of suite.specs ?? []) {
      const results = (spec.tests ?? []).flatMap((test) => test.results ?? []);
      specs.push({
        key: `${spec.file}:${spec.line}:${spec.column} ${spec.title}`,
        file: spec.file,
        results: results.length,
        seconds:
          results.reduce((sum, result) => sum + (result.duration ?? 0), 0) /
          1000,
      });
    }
    for (const child of suite.suites ?? []) walk(child);
  };
  for (const suite of report.suites ?? []) walk(suite);
  return specs;
}

/**
 * Compares the unsharded listing with the merged run. `specFiles` is what the
 * shard plan divides (lib/shard.ts walks the spec roots); a listed file outside
 * it belongs to no shard.
 */
export function checkParity(listed, merged, specFiles) {
  const expected = collectSpecs(listed);
  const actual = collectSpecs(merged);
  const expectedKeys = new Set(expected.map((spec) => spec.key));
  const seen = new Map();
  for (const spec of actual)
    seen.set(spec.key, [...(seen.get(spec.key) ?? []), spec]);
  const planned = new Set(specFiles);
  const problems = {
    // A spec file Playwright runs but the shard plan never hands to a shard.
    unplanned: [...new Set(expected.map((spec) => spec.file))]
      .filter((file) => !planned.has(file))
      .sort(),
    // Listed, absent from every shard's report.
    missing: [...expectedKeys].filter((key) => !seen.has(key)).sort(),
    // Reported by a shard but not in the listing.
    extra: [...seen.keys()].filter((key) => !expectedKeys.has(key)).sort(),
    // Listed and reported, but it never started: a shard stopped early, or
    // an earlier test of a serial group failed (the real failure is then in
    // that shard's own log).
    notRun: [...seen]
      .filter(([, entries]) => entries.every((spec) => spec.results === 0))
      .map(([key]) => key)
      .sort(),
    // Ran in more than one shard. Retries are off, so one test has one result.
    duplicated: [...seen]
      .filter(
        ([, entries]) =>
          entries.length > 1 || entries.some((spec) => spec.results > 1),
      )
      .map(([key]) => key)
      .sort(),
    listedTwice: expected.length - expectedKeys.size,
  };
  const ok =
    !problems.unplanned.length &&
    !problems.missing.length &&
    !problems.extra.length &&
    !problems.notRun.length &&
    !problems.duplicated.length &&
    !problems.listedTwice &&
    expected.length > 0;
  return {
    ok,
    listed: expected.length,
    reported: actual.length,
    files: new Set(expected.map((spec) => spec.file)).size,
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
    const result = checkParity(
      read("--listed"),
      read("--merged"),
      listSpecFiles(),
    );
    console.log(
      `listed ${result.listed} tests in ${result.files} spec files; the shards reported ${result.reported}`,
    );
    for (const [kind, keys] of Object.entries(result.problems)) {
      if (Array.isArray(keys) && keys.length)
        console.log(
          `::error title=E2E shard parity: ${kind}::${keys.slice(0, 20).join(" | ")}`,
        );
    }
    if (result.problems.listedTwice)
      console.log(
        `::error title=E2E shard parity::${result.problems.listedTwice} listed tests share a location and title`,
      );
    if (!result.ok) {
      console.log("The shards did not run exactly the unsharded suite.");
      return 1;
    }
    console.log(
      "Every listed test has exactly one result: the shards ran the whole suite once.",
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
