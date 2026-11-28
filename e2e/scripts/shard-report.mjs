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
import {
  appendFileSync,
  closeSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { execFileSync, spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { listSpecFiles } from "../lib/shard.ts";

const E2E_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNTIME_SKIPS = path.join(E2E_DIR, ".runtime-skips.json");
const STACKS = ["distributed", "standalone"];

/**
 * The tests that may skip themselves while they run (.runtime-skips.json):
 * each entry names a test (a substring of its title, usually the flow id), a
 * stack, a reason and, unless the skip is by design, the ticket that removes
 * it. Fails closed: a broken entry stops the parity check, because an entry
 * is a test that proves nothing on that stack.
 */
const DAY_MS = 24 * 60 * 60 * 1000;
// A temporary entry (one with a ticket) expires at most this many days ahead.
export const MAX_RUNTIME_SKIP_DAYS = 45;

/** Workflow-command data: % and line breaks escaped, as Actions requires. */
export function annotation(text) {
  return String(text)
    .replaceAll("%", "%25")
    .replaceAll("\r", "%0D")
    .replaceAll("\n", "%0A");
}

/**
 * The run's own date (E2E_QUARANTINE_TODAY, pinned when the run was created),
 * never wall time: re-running only the report job cannot change which
 * run-time skip entries have expired.
 */
export function runDate(env = process.env) {
  const day = env.E2E_QUARANTINE_TODAY ?? "";
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(day))
    throw new Error("E2E_QUARANTINE_TODAY must be the run date (YYYY-MM-DD)");
  return new Date(`${day}T00:00:00Z`);
}

export function readRuntimeSkips(file = RUNTIME_SKIPS, today = new Date()) {
  const entries = JSON.parse(readFileSync(file, "utf8"));
  if (!Array.isArray(entries)) throw new Error(`${file}: expected a list`);
  const day = Date.UTC(
    today.getUTCFullYear(),
    today.getUTCMonth(),
    today.getUTCDate(),
  );
  for (const entry of entries) {
    const bad = ["id", "stack", "reason"].filter(
      (field) => typeof entry?.[field] !== "string" || !entry[field].trim(),
    );
    if (bad.length || !STACKS.includes(entry.stack))
      throw new Error(
        `${file}: each entry needs id, reason and stack (${STACKS.join(" or ")}): ${JSON.stringify(entry)}`,
      );
    if (!/^[A-Z]+-E2E-[0-9]{3}$/.test(entry.id))
      throw new Error(
        `${file}: an entry names a flow id such as OBS-E2E-031: ${JSON.stringify(entry)}`,
      );
    if (
      entry.issue !== undefined &&
      (typeof entry.issue !== "string" || !entry.issue.trim())
    )
      throw new Error(
        `${file}: a ticket must be a nonblank string: ${JSON.stringify(entry)}`,
      );
    if (!entry.issue && !/by design/.test(entry.reason))
      throw new Error(
        `${file}: an entry that is not by design names the ticket that removes it: ${JSON.stringify(entry)}`,
      );
    if (entry.issue) {
      // A temporary entry names who removes it and when it stops allowing.
      const expires = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(entry.expires ?? "")
        ? Date.parse(`${entry.expires}T00:00:00Z`)
        : NaN;
      if (
        typeof entry.owner !== "string" ||
        !entry.owner.trim() ||
        Number.isNaN(expires) ||
        expires - day > MAX_RUNTIME_SKIP_DAYS * DAY_MS
      )
        throw new Error(
          `${file}: an entry with a ticket needs an owner and an expires date at most ${MAX_RUNTIME_SKIP_DAYS} days ahead: ${JSON.stringify(entry)}`,
        );
      entry.expired = expires < day;
    }
  }
  return entries;
}

/**
 * Every spec of a JSON report (a listing or a run), flattened. A test is
 * identified by Playwright's own test id, which does not move when a line is
 * added above the test; a report without ids falls back to line and column.
 * The key keeps its five positions (file, identity, column, title, project):
 * qualify-topology.mjs reads the title from it.
 */
function flowId(annotations = []) {
  for (const entry of annotations) {
    if (entry?.type !== "flow") continue;
    try {
      const id = JSON.parse(entry.description ?? "").id;
      if (typeof id === "string") return id;
    } catch {
      // Not a flow annotation this harness wrote.
    }
  }
  return undefined;
}

export function collectSpecs(report) {
  const specs = [];
  const walk = (suite) => {
    for (const spec of suite.specs ?? []) {
      if (!spec.tests?.length)
        throw new Error(`no project test at ${spec.file}:${spec.line}`);
      for (const test of spec.tests) {
        const results = test.results ?? [];
        const annotations = (test.annotations ?? []).map((entry) => entry.type);
        specs.push({
          // The catalogue id (OBS-E2E-031) from the flow annotation, if any.
          flowId: flowId(test.annotations),
          key: JSON.stringify([
            spec.file,
            spec.id ?? spec.line,
            spec.id ? "" : spec.column,
            spec.title,
            test.projectId ?? test.projectName ?? "",
          ]),
          file: spec.file,
          results: results.length,
          // Only the independent listing declares an intentional skip/xfail.
          // A worker changing its own expectedStatus after a runtime skip is
          // not evidence that the test was ineligible.
          expectedStatus: annotations.some(
            (type) => type === "skip" || type === "fixme",
          )
            ? "skipped"
            : annotations.includes("fail")
              ? "failed"
              : test.expectedStatus ?? "passed",
          statuses: results.map((result) => result.status),
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
 * Compares the unsharded listing with the merged run. `specFiles` is what the
 * shard plan divides (lib/shard.ts walks the spec roots); a listed file outside
 * it belongs to no shard.
 */
export function checkParity(
  listed,
  merged,
  specFiles,
  enforceRuntimeSkips = true,
  allowedRuntimeSkips = [],
) {
  const expected = collectSpecs(listed);
  const actual = collectSpecs(merged);
  const expectedKeys = new Set(expected.map((spec) => spec.key));
  const seen = new Map();
  for (const spec of actual)
    seen.set(spec.key, [...(seen.get(spec.key) ?? []), spec]);
  const planned = new Set(specFiles);
  const runtimeSkipped = [];
  const allowedSkips = [];
  const usedAllowances = new Set();
  const wrongOutcome = [];
  // Allowances whose test ran its body and passed: the entry is out of date.
  // A test that failed or never ran says nothing about the skip.
  const passedAllowances = new Set();
  for (const { key, expectedStatus, flowId } of expected) {
    const statuses = (seen.get(key) ?? []).flatMap((entry) => entry.statuses);
    // Exact catalogue id from the independent listing, never a title substring.
    const allowance = allowedRuntimeSkips.find((id) => id === flowId);
    if (expectedStatus === "passed" && statuses.includes("skipped")) {
      if (allowance === undefined) runtimeSkipped.push(key);
      else {
        allowedSkips.push(key);
        usedAllowances.add(allowance);
      }
    } else if (
      allowance !== undefined &&
      statuses.length &&
      statuses.every((status) => status === "passed")
    )
      passedAllowances.add(allowance);
    if (
      !["passed", "skipped", "failed"].includes(expectedStatus) ||
      statuses.some(
        (status) =>
          status !== expectedStatus &&
          !(expectedStatus === "passed" && status === "skipped"),
      )
    ) {
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
    // Skipped at run time and listed in .runtime-skips.json for this stack.
    allowedRuntimeSkips: allowedSkips.sort(),
    // Listed for this stack, and the test ran its body and passed.
    staleAllowances: allowedRuntimeSkips
      .filter((id) => !usedAllowances.has(id) && passedAllowances.has(id))
      .sort(),
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

/** Fails on a stack plan or caller prefix that cannot name the shard jobs. */
export function checkPlan(stack, total, prefix = "") {
  if (
    !["distributed", "standalone"].includes(stack) ||
    !Number.isSafeInteger(total) ||
    total < 1
  )
    throw new Error("invalid stack plan");
  // A caller's jobs prefix these names ("<caller job> / "); none when direct.
  if (prefix !== "" && !/^[A-Za-z0-9][A-Za-z0-9 _.-]* \/ $/.test(prefix))
    throw new Error("invalid job prefix");
}

/**
 * The attempt that produced each shard's receipts, by shard number (1-based).
 *
 * "Re-run failed jobs" starts a new attempt in which each shard that is not
 * re-run appears again as a copy: same name, the new run_attempt, but the
 * original start and end times, and no new artifacts. So a shard's receipts
 * carry the earliest attempt of the copies of its latest job. Fails closed:
 * every shard needs a finished latest job, and the copies must agree.
 */
export function shardAttempts(stack, total, jobs, prefix = "") {
  checkPlan(stack, total, prefix);
  const produced = [];
  for (let i = 1; i <= total; i++) {
    const matches = jobs
      .filter((job) => job.name === `${prefix}e2e (${stack}, ${i}/${total})`)
      .sort((a, b) => b.run_attempt - a.run_attempt);
    if (
      !matches.length ||
      !matches.every(
        (job) => Number.isSafeInteger(job.run_attempt) && job.run_attempt >= 1,
      ) ||
      !matches[0].started_at ||
      !matches[0].completed_at
    )
      throw new Error(`missing producer ${stack}/${i}`);
    if (new Set(matches.map((job) => job.run_attempt)).size !== matches.length)
      throw new Error(`repeated producer ${stack}/${i}`);
    const latest = matches[0];
    const copies = matches.filter(
      (job) =>
        job.started_at === latest.started_at &&
        job.completed_at === latest.completed_at,
    );
    // A copy is one attempt after another back to the producer, never a gap.
    const first = copies[copies.length - 1].run_attempt;
    if (copies.length !== latest.run_attempt - first + 1)
      throw new Error(`inconsistent producer copies ${stack}/${i}`);
    produced.push(first);
  }
  return produced;
}

/**
 * Fails when shards from different attempts ran different pulled images.
 *
 * Each record lists the images its stack's containers ran, with registry
 * digests, and marks the ones the job built from this commit (`built`). Built
 * images are bound by the commit; their digests differ on every shard, so they
 * are never compared. A pulled image can change behind a mutable tag, so a
 * shard carried over from an earlier attempt ("Re-run failed jobs") joins the
 * report only when every shard pulled identical digests. Shards of one attempt
 * ran together and are not compared, as before re-run shards could join.
 */
export function checkStackImages(records, attempts) {
  if (
    !Array.isArray(attempts) ||
    attempts.length !== records.length ||
    !attempts.every((attempt) => Number.isSafeInteger(attempt) && attempt >= 1)
  )
    throw new Error("invalid shard attempts");
  const pulled = records.map((rows, index) => {
    if (
      !Array.isArray(rows) ||
      !rows.length ||
      rows.some(
        (row) =>
          typeof row?.ref !== "string" ||
          typeof row.built !== "boolean" ||
          !Array.isArray(row.digests) ||
          row.digests.some((digest) => typeof digest !== "string"),
      )
    )
      throw new Error(`invalid stack image record for shard ${index + 1}`);
    return new Map(
      rows
        .filter((row) => !row.built && row.digests.length)
        .map((row) => [row.ref, JSON.stringify([...row.digests].sort())]),
    );
  });
  if (new Set(attempts).size === 1) return;
  const refs = new Set(pulled.flatMap((map) => [...map.keys()]));
  const drift = [...refs]
    .filter((ref) => new Set(pulled.map((map) => map.get(ref) ?? "absent")).size > 1)
    .sort();
  if (drift.length)
    throw new Error(
      `shards ran different pulled images (${drift.join(", ")}); rerun all jobs to qualify one stack`,
    );
}

// A GitHub API read can fail for a moment or come back cut short: gh then says
// "unexpected end of JSON input" (run 45334240608). Such a read is retried from
// scratch, a few times with backoff, then fails closed with the exact error. A
// partial response is never used.
export const API_TRIES = 4;
const TRANSIENT =
  /unexpected end of JSON input|HTTP 5\d\d|HTTP 429|rate limit|timed? ?out|connection (?:reset|refused)|\bEOF\b|TLS handshake|temporarily unavailable|Bad Gateway|Service Unavailable|Gateway Timeout/i;

const sleepSync = (ms) =>
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

const failure = (result) => {
  const stderr = String(result.stderr || result.error?.message || "").trim();
  return {
    error: `failed (exit ${result.status}): ${stderr}`,
    transient: Boolean(result.error) || TRANSIENT.test(stderr),
  };
};

/** `gh api ARGS` parsed as JSON, read again from scratch on a transient failure. */
export function ghJson(args, { run = spawnSync, sleep = sleepSync } = {}) {
  const command = ["api", ...args];
  for (let attempt = 1; attempt <= API_TRIES; attempt++) {
    const result = run("gh", command, {
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    });
    let outcome;
    if (result.status === 0) {
      try {
        return JSON.parse(result.stdout);
      } catch (cause) {
        outcome = {
          error: `returned cut-short JSON (${cause.message})`,
          transient: true,
        };
      }
    } else {
      outcome = failure(result);
    }
    if (!outcome.transient || attempt === API_TRIES)
      throw new Error(
        `gh ${command.join(" ")} ${outcome.error}, after ${attempt} tries`,
      );
    sleep(2 ** attempt * 1000);
  }
  throw new Error("unreachable");
}

function sha256File(file) {
  return execFileSync("sha256sum", [file], { encoding: "utf8" }).split(" ")[0];
}

/**
 * Downloads `gh api ARGS` into `file` until its sha256 equals `digest`
 * ("sha256:<hex>"). A transient failure, or a download cut short (which cannot
 * match the digest), is fetched again from scratch; then it fails closed and
 * leaves no file behind.
 */
export function ghDownload(
  args,
  file,
  digest,
  { run = spawnSync, sleep = sleepSync, hash = sha256File } = {},
) {
  const command = ["api", ...args];
  for (let attempt = 1; attempt <= API_TRIES; attempt++) {
    rmSync(file, { force: true });
    const fd = openSync(file, "wx");
    let result;
    try {
      result = run("gh", command, {
        stdio: ["ignore", fd, "pipe"],
        encoding: "utf8",
      });
    } finally {
      closeSync(fd);
    }
    let outcome;
    if (result.status !== 0) outcome = failure(result);
    else if (`sha256:${hash(file)}` === digest) return;
    else outcome = { error: `does not match ${digest}`, transient: true };
    if (!outcome.transient || attempt === API_TRIES) {
      rmSync(file, { force: true });
      throw new Error(
        `gh ${command.join(" ")} ${outcome.error}, after ${attempt} tries`,
      );
    }
    sleep(2 ** attempt * 1000);
  }
  throw new Error("unreachable");
}

/** The one unexpired artifact with this name, or an error. */
export function receiptArtifact(artifacts, name) {
  const matches = artifacts.filter((row) => row.name === name && !row.expired);
  if (matches.length !== 1)
    throw new Error(`missing or repeated receipt: ${name}`);
  return matches[0];
}

// GitHub can list a run's jobs seconds behind the jobs themselves (Backend
// merge group run 45343675711 read its jobs without a shard that had finished
// about four seconds earlier). The report knows every shard
// and receipt it needs, so it reads both listings again, in full, until they
// show all of them, pausing this many seconds between reads (90 in all). Then
// it fails closed, naming what is still missing.
export const SETTLE_PAUSES = [5, 10, 15, 30, 30];

/** This run attempt (GITHUB_RUN_ATTEMPT), or 0 outside Actions. */
export function runAttempt(env) {
  const attempt = Number(env.GITHUB_RUN_ATTEMPT);
  return Number.isSafeInteger(attempt) && attempt >= 1 ? attempt : 0;
}

/**
 * What the listings do not show yet: each shard's job, finished in this run
 * attempt (every attempt lists every job: a shard it did not re-run appears as
 * a copy), and then the receipts the report reads, from the attempt that
 * produced each. Without a known attempt, a shard's latest job counts.
 */
export function unsettled(stack, total, jobs, artifacts, prefix = "", attempt = 0) {
  const missing = [];
  for (let i = 1; i <= total; i++) {
    const name = `${prefix}e2e (${stack}, ${i}/${total})`;
    const listed = jobs.filter((job) => job.name === name);
    const current = attempt
      ? listed.filter((job) => job.run_attempt === attempt)
      : listed.sort((a, b) => b.run_attempt - a.run_attempt).slice(0, 1);
    if (!current.length || !current.every((job) => job.started_at && job.completed_at))
      missing.push(`job ${name}`);
  }
  if (missing.length) return missing;
  let attempts;
  try {
    attempts = shardAttempts(stack, total, jobs, prefix);
  } catch {
    // Not a listing delay: the report fails on it as before.
    return [];
  }
  // An expired receipt counts as listed: waiting cannot bring it back, and
  // the report rejects it at once.
  const listed = new Set(artifacts.map((row) => row.name));
  const wanted = [];
  for (let i = 1; i <= total; i++)
    wanted.push(
      `blob-report-${stack}-${i}-attempt-${attempts[i - 1]}`,
      `stack-images-${stack}-${i}-attempt-${attempts[i - 1]}`,
    );
  wanted.push(`once-per-stack-${stack}-attempt-${attempts[total - 1]}`);
  return wanted.filter((name) => !listed.has(name)).map((name) => `artifact ${name}`);
}

/**
 * Both listings, `read("jobs")` and `read("artifacts")`, each read again in
 * full until `unsettled` finds nothing missing; fails closed after the pauses.
 */
export function settledListings(read, plan, { sleep = sleepSync, log = console.log } = {}) {
  const { stack, total, prefix = "", attempt = 0 } = plan;
  // A plan that cannot name the shards fails now, not after the pauses.
  checkPlan(stack, total, prefix);
  for (const wait of [...SETTLE_PAUSES, null]) {
    const jobs = read("jobs");
    const artifacts = read("artifacts");
    const missing = unsettled(stack, total, jobs, artifacts, prefix, attempt);
    if (!missing.length) return { jobs, artifacts };
    if (wait === null)
      throw new Error(
        `after ${SETTLE_PAUSES.reduce((sum, pause) => sum + pause, 0)} s of reading, the run's listings still do not show ${missing.join(", ")}`,
      );
    log(`The run's listings do not show ${missing.join(", ")} yet; reading them again in ${wait} s`);
    sleep(wait * 1000);
  }
  throw new Error("unreachable");
}

function downloadReceipts(stack, total) {
  const repo = `repos/${process.env.GITHUB_REPOSITORY}/actions`;
  const run = `${repo}/runs/${process.env.GITHUB_RUN_ID}`;
  const prefix = process.env.E2E_JOB_PREFIX ?? "";
  // Every page again on each try: a cut-short listing is never completed.
  const pages = (url, key) =>
    ghJson(["--paginate", "--slurp", url]).flatMap((page) => page[key]);
  const { jobs, artifacts } = settledListings(
    (what) =>
      what === "jobs"
        ? pages(`${run}/jobs?filter=all&per_page=100`, "jobs")
        : pages(`${run}/artifacts?per_page=100`, "artifacts"),
    { stack, total, prefix, attempt: runAttempt(process.env) },
  );
  const attempts = shardAttempts(stack, total, jobs, prefix);
  // The last shard writes the once-per-stack record.
  const recordAttempt = attempts[total - 1];
  const temp = mkdtempSync(path.join(tmpdir(), "e2e-receipts-"));
  const stream = (command, args, file) => {
    const fd = openSync(file, "wx");
    try {
      execFileSync(command, args, { stdio: ["ignore", fd, "inherit"] });
    } finally {
      closeSync(fd);
    }
  };
  const unpack = (name, member, target, attempt) => {
    const { id, digest } = receiptArtifact(artifacts, name);
    const archive = path.join(temp, String(id));
    ghDownload([`${repo}/artifacts/${id}/zip`], archive, digest);
    if (
      execFileSync("unzip", ["-Z1", archive], {
        encoding: "utf8",
      }).trimEnd() !== member
    )
      throw new Error(`unexpected archive members in ${name}`);
    mkdirSync(path.dirname(target), { recursive: true });
    stream("unzip", ["-p", archive, member], target);
    console.log(`${name}: attempt ${attempt}, artifact ${id} ${digest}`);
    rmSync(archive);
  };
  try {
    for (let i = 1; i <= total; i++) {
      const blob = `report-${i}-of-${total}.zip`;
      unpack(
        `blob-report-${stack}-${i}-attempt-${attempts[i - 1]}`,
        blob,
        `all-blob-reports/${blob}`,
        attempts[i - 1],
      );
    }
    // A re-run of failed jobs combines shards of different attempts: only
    // when every shard pulled the same images does the report describe one
    // stack. Every record is still read and validated on a single attempt.
    const images = [];
    for (let i = 1; i <= total; i++) {
      const target = path.join(temp, `stack-images-${i}.json`);
      unpack(
        `stack-images-${stack}-${i}-attempt-${attempts[i - 1]}`,
        "images.json",
        target,
        attempts[i - 1],
      );
      images.push(JSON.parse(readFileSync(target, "utf8")));
    }
    checkStackImages(images, attempts);
    unpack(
      `once-per-stack-${stack}-attempt-${recordAttempt}`,
      "record.json",
      "once-per-stack/record.json",
      recordAttempt,
    );
    const record = JSON.parse(
      readFileSync("once-per-stack/record.json", "utf8"),
    );
    if (
      record.run !== process.env.GITHUB_RUN_ID ||
      record.sha !== process.env.GITHUB_SHA ||
      record.stack !== stack ||
      record.total !== total ||
      record.attempt !== recordAttempt ||
      record.date !== process.env.E2E_QUARANTINE_TODAY ||
      !/^[0-9a-f]{40}$/.test(record.sdk)
    )
      throw new Error(
        "once-per-stack receipt does not match this candidate and producer",
      );
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
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
    const stack =
      process.env.E2E_STACK === "standalone" ? "standalone" : "distributed";
    const entries = readRuntimeSkips(RUNTIME_SKIPS, runDate());
    const result = checkParity(
      read("--listed"),
      read("--merged"),
      listSpecFiles(),
      !args.includes("--phase-runtime-skips") ||
        process.env.E2E_ENFORCE_RUNTIME_SKIPS === "true",
      entries
        .filter((entry) => entry.stack === stack && !entry.expired)
        .map((entry) => entry.id),
    );
    for (const entry of entries)
      if (entry.stack === stack && entry.expired)
        console.log(
          `::warning title=E2E run-time skip entry expired::${annotation(`${entry.id} expired on ${entry.expires} and no longer allows a skip (${entry.issue})`)}`,
        );
    for (const key of result.allowedRuntimeSkips)
      console.log(
        `::warning title=E2E test skipped itself (allowed in .runtime-skips.json)::${annotation(key)}`,
      );
    for (const id of result.staleAllowances)
      console.log(
        `::warning title=E2E run-time skip list is out of date::${annotation(`${id} ran and passed on ${stack}; remove its entry from e2e/.runtime-skips.json`)}`,
      );
    if (args.includes("--out"))
      writeFileSync(
        option(args, "--out"),
        `${JSON.stringify(result, null, 2)}\n`,
      );
    const skipped = result.problems.runtimeSkipped;
    if (process.env.GITHUB_STEP_SUMMARY) {
      const mode = result.enforceRuntimeSkips ? "enforced" : "report-only";
      appendFileSync(
        process.env.GITHUB_STEP_SUMMARY,
        [
          `### E2E shard parity (${process.env.E2E_STACK ?? "all"})`,
          `Listed to pass but skipped at runtime: **${skipped.length + result.allowedRuntimeSkips.length}** (${result.allowedRuntimeSkips.length} allowed by e2e/.runtime-skips.json, ${skipped.length} unexpected; ${mode}).`,
          "",
          ...skipped.map((key) => `    ${key}`),
          "",
        ].join("\n"),
      );
    }
    console.log(
      `listed ${result.listed} tests in ${result.files} spec files; the shards reported ${result.reported}`,
    );
    for (const [kind, keys] of Object.entries(result.problems)) {
      if (Array.isArray(keys) && keys.length) {
        const level =
          kind === "runtimeSkipped" && !result.enforceRuntimeSkips
            ? "warning"
            : "error";
        const sample = annotation(keys.slice(0, 20).join(" | "));
        console.log(
          `::${level} title=E2E shard parity: ${kind}::${keys.length}: ${sample}`,
        );
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
      `Every listed test has exactly one result; ${skipped.length + result.allowedRuntimeSkips.length} listed-to-pass tests skipped at runtime (${result.allowedRuntimeSkips.length} allowed, ${skipped.length} unexpected).`,
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
