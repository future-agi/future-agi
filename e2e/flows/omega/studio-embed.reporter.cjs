/* eslint-env node */
// J07 value-free reporter (E3; ARTIFACT-POLICY value_free_producer). Runs beside
// the line reporter. It reads only the spec's `j07-record` annotations, which the
// spec builds from allowlisted fields, re-validates every field here, and writes
// one create-only JSON file of assertion ids, counts, booleans and case/browser
// outcomes. It never prints, never reads attachments, stdout, stderr, error
// messages or test steps, and never copies a title it did not recognise.
//
//   reporter: [['line'], ['./flows/omega/studio-embed.reporter.cjs',
//              { schema: 'j07-value-free/v1', outputFile: '/out/J07-ASSERTIONS.json' }]]
//
// Completeness is judged against the FIXED eleven-case CASES list for every engine
// the invocation selected (config.projects[*].use.browserName), never against the
// tests that happened to be collected: a case that was filtered out (--grep),
// renamed, duplicated or never ran becomes a not_covered/fail cell and onEnd
// returns a failed status. A passing cell must also carry a pass record for every
// assertion its case owns. After the run, every project output directory is
// inventoried (counts only): any error-context or capture file fails the run.
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const SCHEMA = "j07-value-free/v1";
const RECORD_TYPE = "j07-record";
const BLOCKED_TYPE = "j07-blocked";
const ENGINES = Object.freeze(["chromium", "firefox", "webkit"]);

/** The eleven integrated case ids, in packet order (PACKET.json new_specs.test_ids). */
const CASES = Object.freeze([
  "J07-01 printed-link-and-first-script",
  "J07-02 storage-and-history",
  "J07-03 exact-window-origin-handshake",
  "J07-04 route-and-theme-precedence",
  "J07-05 wrong-origin-and-wrong-window",
  "J07-06 stream-drop-and-restore",
  "J07-06b server-restart-key-rotation",
  "J07-07 request-referer-and-server-log-audit",
  "J07-08 protocol-fixture-byte-parity",
  "J07-09 admission-and-inert-config",
  "J07-10 token-safe-artifact-policy",
]);

/** The assertions each integrated case must PASS (A30 adopts J00; A35/A36 are the envelope). */
const CASE_ASSERTIONS = Object.freeze({
  "J07-01": ["A01", "A02", "A03", "A04"],
  "J07-02": ["A05", "A06", "A07", "A08"],
  "J07-03": ["A09", "A10", "A11", "A12", "A29"],
  "J07-04": ["A13", "A14", "A15", "A16"],
  "J07-05": ["A17", "A18", "A19", "A20"],
  "J07-06": ["A21", "A22", "A06"],
  "J07-06b": ["A23"],
  "J07-07": ["A24", "A25", "A26", "A27", "A34"],
  "J07-08": ["A28"],
  "J07-09": ["A31", "A32", "A33", "A34"],
  "J07-10": ["A37"],
});

const ENUM = (values) => (value) => typeof value === "string" && values.includes(value);
const PATTERN = (re) => (value) => typeof value === "string" && re.test(value);
const COUNT = (value) => Number.isInteger(value) && value >= 0 && value <= 1e9;
const MS = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e9;

// Field validators: enums or lowercase/short patterns, so a token-shaped value
// (43 mixed-case base64url characters) can never pass as a string field.
const ALLOWED = Object.freeze({
  schema: ENUM([SCHEMA]),
  case_id: PATTERN(/^J07-\d{2}b?$/),
  assertion_id: PATTERN(/^A\d{2}$/),
  control_id: PATTERN(/^C\d{2}(?:-[a-z0-9]+){0,4}$/),
  browser: ENUM(ENGINES),
  invocation_id: PATTERN(/^[a-z0-9][a-z0-9.-]{0,47}$/),
  context_ordinal: COUNT,
  request_ordinal: COUNT,
  phase: ENUM(["precondition", "login", "connect", "embedded", "navigation", "theme", "hostile",
    "stream", "cut", "restore", "restart", "audit", "config", "control", "blocked"]),
  outcome: ENUM(["pass", "fail", "blocked", "not_covered", "observed"]),
  count: COUNT,
  elapsed_ms: MS,
  role_alias: PATTERN(/^org[ab]-user[12]$|^anonymous$/),
  run_alias: PATTERN(/^r\d{1,3}$/),
  http_status: (value) => Number.isInteger(value) && value >= 0 && value <= 999,
  method_enum: ENUM(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
  route_template_id: PATTERN(/^[a-z][a-z0-9.-]{0,47}$/),
  origin_slot: ENUM(["fa-app", "fa-api", "studio", "studio-proxy", "hostile", "hostile-parent", "sibling",
    "same-origin-sibling", "replacement", "none"]),
  comparison_boolean: (value) => typeof value === "boolean",
});

/** Returns the list of offending field names (empty: the record is value-free). */
function recordViolations(record) {
  if (!record || typeof record !== "object" || Array.isArray(record)) return ["record"];
  const bad = [];
  for (const [key, value] of Object.entries(record)) {
    const ok = Object.prototype.hasOwnProperty.call(ALLOWED, key) && ALLOWED[key](value);
    if (!ok) bad.push(Object.prototype.hasOwnProperty.call(ALLOWED, key) ? key : "unknown-field");
  }
  if (!ALLOWED.case_id(record.case_id)) bad.push("case_id");
  if (!ALLOWED.outcome(record.outcome)) bad.push("outcome");
  return bad;
}

/** The case id of an EXACT required title, or null (renamed titles are unrecognized). */
const caseOf = (title) => {
  if (!CASES.includes(title)) return null;
  return /^(J07-\d{2}b?) /.exec(title)[1];
};

const browserOfProject = (project) => {
  const name = project?.use?.browserName ?? (project ? "chromium" : undefined);
  return ALLOWED.browser(name) ? name : null;
};

const browserOf = (test) => browserOfProject(test.parent?.project?.());

const ERROR_CONTEXT = /^error-context(?:-\d+)?\.md$/i;
const CAPTURE_FILE = /\.(?:zip|png|jpe?g|webm|har|trace|network)$/i;

/** Value-free inventory of output directories: files, error-context files, capture artifacts. */
function artifactInventory(dirs) {
  const out = { files: 0, errorContext: 0, capture: 0, unreadable: 0 };
  const walk = (current) => {
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (error) {
      if (error && error.code !== "ENOENT") out.unreadable += 1;
      return;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else {
        out.files += 1;
        if (ERROR_CONTEXT.test(entry.name)) out.errorContext += 1;
        if (CAPTURE_FILE.test(entry.name)) out.capture += 1;
      }
    }
  };
  for (const dir of new Set(dirs)) if (typeof dir === "string" && dir !== "") walk(dir);
  return out;
}

/**
 * Pure completeness verdict over the fixed CASES x engines matrix.
 *   engines: selected engines; discovered: Map(key -> count of collected tests);
 *   ended: Map(key -> { outcome, ends, passed: Set(assertion ids) }); unrecognized: count
 */
function completeness({ engines, discovered, ended, unrecognized, violations = 0, globalErrors = 0, artifacts = null }) {
  const cells = [];
  let duplicates = 0;
  for (const browser of engines) {
    for (const title of CASES) {
      const case_id = caseOf(title);
      const key = `${case_id}|${browser}`;
      const found = discovered.get(key) ?? 0;
      const end = ended.get(key);
      if (found > 1 || (end && end.ends > 1)) duplicates += 1;
      let outcome = end ? end.outcome : "not_covered";
      if (found === 0) outcome = "not_covered";
      const required = CASE_ASSERTIONS[case_id] ?? [];
      const missing = outcome === "pass" ? required.filter((id) => !end.passed.has(id)).length : 0;
      if (outcome === "pass" && missing > 0) outcome = "fail";
      cells.push({ case_id, browser, outcome, missing_assertions: missing });
    }
  }
  const engineOk = engines.length > 0 && engines.every((name) => ENGINES.includes(name)) && new Set(engines).size === engines.length;
  const artifactsClean = artifacts === null || (artifacts.errorContext === 0 && artifacts.capture === 0 && artifacts.unreadable === 0);
  const complete = engineOk
    && cells.length === CASES.length * engines.length
    && cells.every((cell) => cell.outcome === "pass")
    && duplicates === 0
    && unrecognized === 0
    && violations === 0
    && globalErrors === 0
    && artifactsClean;
  return { cells, duplicates, complete };
}

class J07ValueFreeReporter {
  constructor(options = {}) {
    // DERIVATION.json stage_a_input_draft.reporter names the option `outputPath`.
    this.outputFile = options.outputFile ?? options.outputPath ?? process.env.J07_ASSERTIONS_OUT ?? null;
    this.records = [];
    this.ended = new Map();
    this.discovered = new Map();
    this.engines = [];
    this.outputDirs = [];
    this.unrecognized = 0;
    this.violations = 0;
    this.globalErrors = 0;
  }

  printsToStdio() {
    return false;
  }

  onBegin(config, suite) {
    // The engines this invocation selected (--browser=<engine> makes one project per engine).
    const engines = [];
    for (const project of config.projects ?? []) {
      const name = browserOfProject(project);
      if (name === null) this.unrecognized += 1;
      else if (!engines.includes(name)) engines.push(name);
      if (typeof project.outputDir === "string") this.outputDirs.push(project.outputDir);
    }
    this.engines = engines;
    for (const test of suite.allTests()) {
      const id = caseOf(test.title);
      const browser = browserOf(test);
      if (!id || !browser) {
        this.unrecognized += 1;
        continue;
      }
      const key = `${id}|${browser}`;
      this.discovered.set(key, (this.discovered.get(key) ?? 0) + 1);
    }
  }

  onTestEnd(test, result) {
    const id = caseOf(test.title);
    const browser = browserOf(test);
    if (!id || !browser) return;
    const key = `${id}|${browser}`;
    const annotations = [...(test.annotations ?? []), ...(result.annotations ?? [])];
    const seen = new Set();
    const passed = new Set();
    let blocked = false;
    for (const annotation of annotations) {
      if (annotation?.type === BLOCKED_TYPE) blocked = true;
      if (annotation?.type !== RECORD_TYPE || typeof annotation.description !== "string") continue;
      if (seen.has(annotation.description)) continue;
      seen.add(annotation.description);
      let record;
      try {
        record = JSON.parse(annotation.description);
      } catch {
        this.violations += 1;
        continue;
      }
      if (recordViolations(record).length > 0 || record.case_id !== id || record.browser !== browser) {
        this.violations += 1;
        continue;
      }
      if (record.outcome === "pass" && typeof record.assertion_id === "string") passed.add(record.assertion_id);
      this.records.push(record);
    }
    // A worker-fixture refusal cannot annotate the test; its constant message says so.
    // Only the fixed prefix is read here, in memory; no error text is ever written.
    if (!blocked && (result.errors ?? []).some((error) => typeof error?.message === "string" && /J07-BLOCKED [A-Z0-9_]+/.test(error.message)))
      blocked = true;
    const status = blocked ? "blocked" : result.status;
    const outcome =
      status === "passed" ? "pass" : status === "blocked" ? "blocked" : status === "skipped" ? "not_covered" : "fail";
    const previous = this.ended.get(key);
    this.ended.set(key, { outcome: previous ? "fail" : outcome, ends: (previous?.ends ?? 0) + 1, passed });
  }

  onError() {
    this.globalErrors += 1;
  }

  async onEnd() {
    const artifacts = artifactInventory(this.outputDirs);
    const verdict = completeness({
      engines: this.engines,
      discovered: this.discovered,
      ended: this.ended,
      unrecognized: this.unrecognized,
      violations: this.violations,
      globalErrors: this.globalErrors,
      artifacts,
    });
    const cells = verdict.cells;
    const summary = {
      schema: SCHEMA,
      invocation_id: ALLOWED.invocation_id(process.env.J07_INVOCATION_ID) ? process.env.J07_INVOCATION_ID : "unbound",
      cases_expected: CASES.length,
      engines: this.engines.length,
      cells,
      records: this.records,
      counts: {
        cells: cells.length,
        passed: cells.filter((cell) => cell.outcome === "pass").length,
        blocked: cells.filter((cell) => cell.outcome === "blocked").length,
        not_covered: cells.filter((cell) => cell.outcome === "not_covered").length,
        missing_assertions: cells.reduce((sum, cell) => sum + cell.missing_assertions, 0),
        duplicates: verdict.duplicates,
        unrecognized: this.unrecognized,
        records: this.records.length,
        record_violations: this.violations,
        global_errors: this.globalErrors,
        artifact_files: artifacts.files,
        error_context_files: artifacts.errorContext,
        capture_files: artifacts.capture,
        unreadable_dirs: artifacts.unreadable,
      },
    };
    if (this.outputFile) {
      fs.mkdirSync(path.dirname(this.outputFile), { recursive: true });
      // Create-only: an existing file is never overwritten or appended to.
      const fd = fs.openSync(this.outputFile, "wx", 0o600);
      try {
        fs.writeSync(fd, `${JSON.stringify(summary, null, 2)}\n`);
      } finally {
        fs.closeSync(fd);
      }
    }
    return verdict.complete ? undefined : { status: "failed" };
  }
}

module.exports = J07ValueFreeReporter;
module.exports.CASES = CASES;
module.exports.CASE_ASSERTIONS = CASE_ASSERTIONS;
module.exports.RECORD_TYPE = RECORD_TYPE;
module.exports.BLOCKED_TYPE = BLOCKED_TYPE;
module.exports.SCHEMA = SCHEMA;
module.exports.artifactInventory = artifactInventory;
module.exports.completeness = completeness;
module.exports.recordViolations = recordViolations;
