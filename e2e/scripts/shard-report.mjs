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
import { appendFileSync, closeSync, mkdirSync, mkdtempSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { listSpecFiles } from "../lib/shard.ts";

const E2E_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Every spec of a JSON report (a listing or a run), flattened. */
export function collectSpecs(report) {
  const specs = [];
  const walk = (suite) => {
    for (const spec of suite.specs ?? []) {
      if (!spec.tests?.length) throw new Error(`no project test at ${spec.file}:${spec.line}`);
      for (const test of spec.tests) {
        const results = test.results ?? [];
        const annotations = (test.annotations ?? []).map((entry) => entry.type);
        specs.push({
          key: JSON.stringify([spec.file, spec.line, spec.column, spec.title, test.projectId ?? test.projectName ?? ""]),
          file: spec.file,
          results: results.length,
          // Only the independent listing declares an intentional skip/xfail.
          // A worker changing its own expectedStatus after a runtime skip is
          // not evidence that the test was ineligible.
          expectedStatus: annotations.some((type) => type === "skip" || type === "fixme")
            ? "skipped" : annotations.includes("fail") ? "failed" : test.expectedStatus ?? "passed",
          statuses: results.map((result) => result.status),
          seconds: results.reduce((sum, result) => sum + (result.duration ?? 0), 0) / 1000,
        });
      }
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
export function checkParity(listed, merged, specFiles, enforceRuntimeSkips = true) {
  const expected = collectSpecs(listed);
  const actual = collectSpecs(merged);
  const expectedKeys = new Set(expected.map((spec) => spec.key));
  const seen = new Map();
  for (const spec of actual)
    seen.set(spec.key, [...(seen.get(spec.key) ?? []), spec]);
  const planned = new Set(specFiles);
  const runtimeSkipped = [];
  const wrongOutcome = [];
  for (const { key, expectedStatus } of expected) {
    const statuses = (seen.get(key) ?? []).flatMap((entry) => entry.statuses);
    if (expectedStatus === "passed" && statuses.includes("skipped")) runtimeSkipped.push(key);
    if (!["passed", "skipped", "failed"].includes(expectedStatus) || statuses.some((status) =>
      status !== expectedStatus && !(expectedStatus === "passed" && status === "skipped"))) {
      wrongOutcome.push(key);
    }
  }
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
    runtimeSkipped: runtimeSkipped.sort(),
    wrongOutcome: wrongOutcome.sort(),
    listedTwice: expected.length - expectedKeys.size,
  };
  const ok =
    !problems.unplanned.length &&
    !problems.missing.length &&
    !problems.extra.length &&
    !problems.notRun.length &&
    !problems.duplicated.length &&
    !problems.listedTwice &&
    !problems.wrongOutcome.length &&
    (!enforceRuntimeSkips || !problems.runtimeSkipped.length) &&
    expected.length > 0;
  return {
    ok,
    enforceRuntimeSkips,
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

// The stack image set is not independently digest-pinned across producer
// attempts yet. Reuse a complete, unsuperseded stack (e.g. a report-only
// retry); require rerunning the cohort rather than combining unlike stacks.
export function cohortAttempt(stack, total, jobs) {
  if (!["distributed", "standalone"].includes(stack) || !Number.isSafeInteger(total) || total < 1)
    throw new Error("invalid stack plan");
  const attempts = new Set();
  for (let i = 1; i <= total; i++) {
    const matches = jobs.filter(job => job.name === `e2e (${stack}, ${i}/${total})`)
      .sort((a, b) => b.run_attempt - a.run_attempt);
    if (!matches.length || !Number.isSafeInteger(matches[0].run_attempt) || matches[0].run_attempt < 1)
      throw new Error(`missing producer ${stack}/${i}`);
    attempts.add(matches[0].run_attempt);
  }
  if (attempts.size !== 1) throw new Error(`mixed ${stack} producers; rerun all jobs to qualify one stack`);
  return [...attempts][0];
}

function downloadReceipts(stack, total) {
  const repo = `repos/${process.env.GITHUB_REPOSITORY}/actions`;
  const run = `${repo}/runs/${process.env.GITHUB_RUN_ID}`;
  const pages = (url, key) => JSON.parse(execFileSync("gh", ["api", "--paginate", "--slurp", url],
    {encoding: "utf8", maxBuffer: 64 * 1024 * 1024})).flatMap(page => page[key]);
  const attempt = cohortAttempt(stack, total, pages(`${run}/jobs?filter=all&per_page=100`, "jobs"));
  const artifacts = pages(`${run}/artifacts?per_page=100`, "artifacts");
  const temp = mkdtempSync(path.join(tmpdir(), "e2e-receipts-"));
  const stream = (command, args, file) => {
    const fd = openSync(file, "wx");
    try { execFileSync(command, args, {stdio: ["ignore", fd, "inherit"]}); }
    finally { closeSync(fd); }
  };
  const unpack = (name, member, target) => {
    const matches = artifacts.filter(row => row.name === name && !row.expired);
    if (matches.length !== 1) throw new Error(`missing or repeated receipt: ${name}`);
    const {id, digest} = matches[0];
    const archive = path.join(temp, String(id));
    stream("gh", ["api", `${repo}/artifacts/${id}/zip`], archive);
    const hash = execFileSync("sha256sum", [archive], {encoding: "utf8"}).split(" ")[0];
    if (digest !== `sha256:${hash}`) throw new Error(`artifact ${id} digest mismatch`);
    if (execFileSync("unzip", ["-Z1", archive], {encoding:"utf8"}).trimEnd() !== member)
      throw new Error(`unexpected archive members in ${name}`);
    mkdirSync(path.dirname(target), {recursive: true});
    stream("unzip", ["-p", archive, member], target);
    console.log(`${name}: attempt ${attempt}, artifact ${id} ${digest}`);
    rmSync(archive);
  };
  try {
    for (let i = 1; i <= total; i++) {
      const blob = `report-${i}-of-${total}.zip`;
      unpack(`blob-report-${stack}-${i}-attempt-${attempt}`, blob, `all-blob-reports/${blob}`);
    }
    unpack(`once-per-stack-${stack}-attempt-${attempt}`, "record.json", "once-per-stack/record.json");
    const record = JSON.parse(readFileSync("once-per-stack/record.json", "utf8"));
    if (record.run !== process.env.GITHUB_RUN_ID || record.sha !== process.env.GITHUB_SHA ||
        record.stack !== stack || record.total !== total || record.attempt !== attempt ||
        record.date !== process.env.E2E_QUARANTINE_TODAY || !/^[0-9a-f]{40}$/.test(record.sdk))
      throw new Error("once-per-stack receipt does not match this candidate and producer");
  } finally { rmSync(temp, {recursive: true, force: true}); }
}

function option(args, name) {
  const at = args.indexOf(name);
  if (at === -1 || !args[at + 1]) throw new Error(`missing ${name} <file>`);
  return args[at + 1];
}

function main([command, ...args]) {
  const read = (name) => JSON.parse(readFileSync(option(args, name), "utf8"));
  if (command === "receipts") {
    downloadReceipts(process.env.E2E_STACK, Number(process.env.SHARD_TOTAL));
    return 0;
  }
  if (command === "parity") {
    const result = checkParity(
      read("--listed"),
      read("--merged"),
      listSpecFiles(),
      !args.includes("--phase-runtime-skips") || process.env.E2E_ENFORCE_RUNTIME_SKIPS === "true",
    );
    if (args.includes("--out")) writeFileSync(option(args, "--out"), `${JSON.stringify(result, null, 2)}\n`);
    const skipped = result.problems.runtimeSkipped;
    if (process.env.GITHUB_STEP_SUMMARY) {
      const mode = result.enforceRuntimeSkips ? "enforced" : "report-only";
      appendFileSync(process.env.GITHUB_STEP_SUMMARY, [
        `### E2E shard parity (${process.env.E2E_STACK ?? "all"})`,
        `Listed to pass but skipped at runtime: **${skipped.length}** (${mode}).`,
        "", ...skipped.map((key) => `    ${key}`), "",
      ].join("\n"));
    }
    console.log(
      `listed ${result.listed} tests in ${result.files} spec files; the shards reported ${result.reported}`,
    );
    for (const [kind, keys] of Object.entries(result.problems)) {
      if (Array.isArray(keys) && keys.length) {
        const level = kind === "runtimeSkipped" && !result.enforceRuntimeSkips ? "warning" : "error";
        const sample = keys.slice(0, 20).join(" | ").replaceAll("%", "%25").replaceAll("\r", "%0D").replaceAll("\n", "%0A");
        console.log(`::${level} title=E2E shard parity: ${kind}::${keys.length}: ${sample}`);
      }
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
      `Every listed test has exactly one result; ${skipped.length} listed-to-pass tests skipped at runtime.`,
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
    "usage: shard-report.mjs parity --listed <json> --merged <json> [--phase-runtime-skips] [--out <file>] | durations --merged <json> [--out <file>]",
  );
  return 2;
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  process.exitCode = main(process.argv.slice(2));
}
