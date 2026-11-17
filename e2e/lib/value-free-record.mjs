// HR-JRN-01 (S2): the CLOSED record schema of everything value-free mode publishes as a
// report record. A record is a JSON document the harness itself publishes, as the reporter that wrote it serializes it:
//   reports/json-<n>.json        Playwright's json report                       (json-report)
//   reports/counts-<n>.json      walks/vm/counts-reporter.cjs's numeric summary (counts-report)
//   reports/blob-<n>.zip         Playwright's blob report: report.jsonl, one event per line (blob-event); resources/<file>
//                                are attachment payloads
//   html(-<n>)/index.html        Playwright's HTML report: its embedded zip's report.json (html-report) and <fileId>.json
//                                (html-file); every other file of the report is the viewer or an attachment payload
//   MANIFEST.json, REFUSED.json  the seal and the refusal record (lib/value-free-seal.mjs)
//   runner.log                   the runner's stdout and stderr, merged (runner-log, R2): lines of text (checkLog)
// Any other file below reports/ is a record of no known kind. Each record type admits a closed vocabulary of field names,
// and each field one kind: a name (a string, sanitized), a type (one of a fixed list), a count (a finite number), a boolean,
// a hash (an id or a digest), a payload (an attachment's body, decoded and sanitized as content, never read as a record),
// another record type, a list of one kind, or an open container Playwright fills with arbitrary data (config and project
// metadata, reporter options, webServer), which is projected to its empty value. null is any field's empty value.
// An unknown field, a value of a kind its field does not admit (an arbitrary nested object included), a document that is
// not one record, or a file of no known kind REFUSES the publication: the builder (lib/value-free-codec.ts) publishes only
// records that validate, projected, and the publication scan and the final check (lib/value-free-reading.mjs Scan) refuse
// any record that does not, before reading any further into it. Payloads (attachment bodies, the HTML viewer,
// attachments/) stay what they were: sanitized and scanned through every layer, never a record.
// S2: runner.log is a record too, not a payload. Its closed schema admits one kind, a line of
// text (a name, as a title, a message or a stack is in every other record); a document in it is a record the runner log
// admits no kind for and REFUSES the publication (checkLog): at assembly (the builder), in the publication scan, in the final
// check, and in the output the runner prints at its exit. A json or junit reporter with no output file would write its report
// into it: the plan refuses that reporter before any worker (lib/value-free-publish.ts). Plain Node, no dependencies but the
// refusal vocabulary.
import { isCleanReason } from "./value-free-vocabulary.mjs";

export const PUBLISH_FORMAT = "e2e-value-free-publish/1";
export const REFUSAL_FORMAT = "e2e-value-free-refusal/1";

/** The two refusal codes of this schema (lib/value-free-vocabulary.mjs REASON_CODES). */
export const UNKNOWN_FIELD = "unknown-field";
export const UNKNOWN_KIND = "unknown-kind";

const N = "name";
const C = "count";
const B = "boolean";
const H = "hash";
const P = "payload";
const type = (...values) => ({ type: values });
const R = (name) => ({ record: name });
const L = (spec) => ({ list: spec });
const any = (...specs) => ({ any: specs });
/** An open container: projected to `empty` when published, and refused when a publication holds anything else there. */
const empty = (value) => ({ empty: value });

const STATUS = type("passed", "failed", "timedOut", "skipped", "interrupted");
const OUTCOME = type("expected", "unexpected", "flaky", "skipped");
const LOC = R("location");
const NAMES = any(N, L(N));

const KINDS = {
  [N]: (v) => typeof v === "string",
  [P]: (v) => typeof v === "string",
  [C]: (v) => typeof v === "number" && Number.isFinite(v),
  [B]: (v) => typeof v === "boolean",
  [H]: (v) =>
    typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(v),
};

/** The blob report's events, by method: the record type of its params (null: an event with none). onStdIO never reaches a
 * publication (the builder drops it: runner.log is the only publication of the output). */
const BLOB_EVENTS = {
  onBlobReportMetadata: "blob-metadata",
  onConfigure: "blob-configure",
  onProject: "blob-project-event",
  onBegin: null,
  onTestBegin: "blob-test-begin",
  onTestEnd: "blob-test-end",
  onStepBegin: "blob-step-begin",
  onStepEnd: "blob-step-end",
  onAttach: "blob-attach",
  onError: "blob-error",
  onEnd: "blob-end",
};

/** Every record type: its fields and each field's kind (or a check of its own). Playwright 1.62's serializers
 * (reporters/json.ts, teleEmitter.ts and blob.ts, html.ts) and walks/vm/counts-reporter.cjs, field by field. */
export const RECORDS = {
  location: { file: N, line: C, column: C },
  error: {
    message: N,
    stack: N,
    value: N,
    snippet: N,
    location: LOC,
    cause: R("error"),
  },
  annotation: { type: N, description: N, location: LOC },
  attachment: { name: N, contentType: N, path: N, body: P },
  shard: { current: C, total: C },
  // reports/json-<n>.json
  "json-report": {
    config: R("json-config"),
    suites: L(R("json-suite")),
    errors: L(R("error")),
    stats: R("json-stats"),
  },
  "json-config": {
    argv: L(N),
    configFile: N,
    failOnFlakyTests: B,
    forbidOnly: B,
    fullyParallel: B,
    globalSetup: NAMES,
    globalTeardown: NAMES,
    globalTimeout: C,
    grep: empty({}),
    grepInvert: empty({}),
    maxFailures: C,
    metadata: empty({}),
    preserveOutput: N,
    projects: L(R("json-project")),
    quiet: B,
    reporter: empty([]),
    reportSlowTests: R("slow-tests"),
    rootDir: N,
    shard: R("shard"),
    tags: L(N),
    updateSnapshots: N,
    updateSourceMethod: N,
    version: N,
    webServer: empty(null),
    workers: C,
  },
  "slow-tests": { max: C, threshold: C },
  "json-project": {
    outputDir: N,
    repeatEach: C,
    retries: C,
    metadata: empty({}),
    id: N,
    name: N,
    testDir: N,
    testIgnore: L(N),
    testMatch: L(N),
    timeout: C,
  },
  "json-suite": {
    title: N,
    file: N,
    line: C,
    column: C,
    specs: L(R("json-spec")),
    suites: L(R("json-suite")),
  },
  "json-spec": {
    title: N,
    ok: B,
    tags: L(N),
    tests: L(R("json-test")),
    id: H,
    file: N,
    line: C,
    column: C,
  },
  "json-test": {
    timeout: C,
    annotations: L(R("annotation")),
    expectedStatus: STATUS,
    projectId: N,
    projectName: N,
    results: L(R("json-result")),
    status: OUTCOME,
  },
  "json-result": {
    workerIndex: C,
    parallelIndex: C,
    status: STATUS,
    duration: C,
    error: R("error"),
    errors: L(R("json-error")),
    stdout: L(R("stdio")),
    stderr: L(R("stdio")),
    retry: C,
    steps: L(R("json-step")),
    startTime: N,
    annotations: L(R("annotation")),
    attachments: L(R("attachment")),
    errorLocation: LOC,
  },
  "json-error": { message: N, location: LOC },
  stdio: { text: N, buffer: P },
  "json-step": {
    title: N,
    duration: C,
    error: R("error"),
    steps: L(R("json-step")),
  },
  "json-stats": {
    startTime: N,
    duration: C,
    expected: C,
    skipped: C,
    unexpected: C,
    flaky: C,
  },
  // reports/counts-<n>.json
  "counts-report": {
    planned: C,
    completed: C,
    passed: C,
    failed: C,
    timedOut: C,
    skipped: C,
    interrupted: C,
    errors: C,
    failures: L(R("counts-failure")),
    status: type("passed", "failed", "timedout", "interrupted", "unknown"),
    durationMs: C,
  },
  "counts-failure": { status: STATUS, frames: L(LOC) },
  // reports/blob-<n>.zip: report.jsonl
  "blob-event": blobEvent,
  "blob-metadata": {
    version: C,
    userAgent: N,
    name: N,
    shard: R("shard"),
    pathSeparator: N,
  },
  "blob-configure": { config: R("blob-config") },
  "blob-config": {
    configFile: N,
    globalTimeout: C,
    maxFailures: C,
    metadata: empty({}),
    rootDir: N,
    shard: R("shard"),
    version: N,
    workers: C,
    globalSetup: NAMES,
    globalTeardown: NAMES,
    tags: L(N),
    webServer: empty(null),
    failOnFlakyTests: B,
  },
  "blob-project-event": { project: R("blob-project") },
  "blob-project": {
    metadata: empty({}),
    name: N,
    outputDir: N,
    repeatEach: C,
    retries: C,
    testDir: N,
    testIgnore: L(R("pattern")),
    testMatch: L(R("pattern")),
    timeout: C,
    suites: L(R("blob-entry")),
    grep: L(R("pattern")),
    grepInvert: L(R("pattern")),
    dependencies: L(N),
    snapshotDir: N,
    teardown: N,
    ignoreSnapshots: B,
    use: R("blob-use"),
  },
  pattern: { s: N, r: R("regex") },
  regex: { source: N, flags: N },
  "blob-use": { testIdAttribute: N },
  // A suite or a test of a project's tree (teleEmitter _serializeSuite and _serializeTest).
  "blob-entry": {
    title: N,
    location: LOC,
    entries: L(R("blob-entry")),
    testId: H,
    retries: C,
    tags: L(N),
    repeatEachIndex: C,
    annotations: L(R("annotation")),
  },
  "blob-test-begin": { testId: H, result: R("blob-result-start") },
  "blob-result-start": {
    id: H,
    retry: C,
    workerIndex: C,
    parallelIndex: C,
    startTime: C,
  },
  "blob-test-end": { test: R("blob-test"), result: R("blob-result-end") },
  "blob-test": {
    testId: H,
    expectedStatus: STATUS,
    timeout: C,
    annotations: L(R("annotation")),
  },
  "blob-result-end": {
    id: H,
    duration: C,
    status: STATUS,
    errors: L(R("error")),
    annotations: L(R("annotation")),
  },
  "blob-step-begin": { testId: H, resultId: H, step: R("blob-step-start") },
  "blob-step-start": {
    id: H,
    parentStepId: H,
    title: N,
    category: N,
    startTime: C,
    location: LOC,
  },
  "blob-step-end": { testId: H, resultId: H, step: R("blob-step-finish") },
  "blob-step-finish": {
    id: H,
    duration: C,
    error: R("error"),
    attachments: L(C),
    annotations: L(R("annotation")),
  },
  "blob-attach": {
    testId: H,
    resultId: H,
    attachments: L(R("blob-attachment")),
  },
  "blob-attachment": { name: N, contentType: N, path: N, base64: P },
  "blob-error": { error: R("error"), workerInfo: R("worker-info") },
  "worker-info": { workerIndex: C, parallelIndex: C, projectName: N },
  "blob-end": { result: R("blob-run-result") },
  "blob-run-result": {
    status: type("passed", "failed", "timedout", "interrupted"),
    startTime: C,
    duration: C,
  },
  // html(-<n>)/index.html: the embedded zip's report.json and <fileId>.json
  "html-report": {
    metadata: empty({}),
    startTime: C,
    duration: C,
    files: L(R("html-file-summary")),
    projectNames: L(N),
    stats: R("html-stats"),
    errors: L(N),
    options: R("html-options"),
    machines: L(R("html-machine")),
  },
  "html-options": { title: N, noSnippets: B, noCopyPrompt: B, mergeFiles: B },
  "html-machine": { duration: C, startTime: C, tag: L(N), shardIndex: C },
  "html-stats": {
    total: C,
    expected: C,
    unexpected: C,
    flaky: C,
    skipped: C,
    ok: B,
  },
  "html-file-summary": {
    fileId: H,
    fileName: N,
    tests: L(R("html-test-summary")),
    stats: R("html-stats"),
  },
  "html-test-summary": {
    testId: H,
    title: N,
    projectName: N,
    location: LOC,
    duration: C,
    annotations: L(R("annotation")),
    tags: L(N),
    outcome: OUTCOME,
    path: L(N),
    ok: B,
    repeatEachIndex: C,
    results: L(R("html-result-summary")),
  },
  "html-result-summary": {
    attachments: L(R("attachment")),
    startTime: N,
    workerIndex: C,
  },
  "html-file": { fileId: H, fileName: N, tests: L(R("html-test")) },
  "html-test": {
    testId: H,
    title: N,
    projectName: N,
    location: LOC,
    duration: C,
    annotations: L(R("annotation")),
    tags: L(N),
    outcome: OUTCOME,
    path: L(N),
    results: L(R("html-result")),
    repeatEachIndex: C,
    ok: B,
  },
  "html-result": {
    duration: C,
    startTime: N,
    retry: C,
    steps: L(R("html-step")),
    errors: L(R("html-error")),
    status: STATUS,
    annotations: L(R("annotation")),
    attachments: L(R("attachment")),
    workerIndex: C,
  },
  "html-error": { message: N, codeframe: N },
  "html-step": {
    title: N,
    startTime: N,
    duration: C,
    steps: L(R("html-step")),
    attachments: L(C),
    location: LOC,
    error: N,
    count: C,
    skipped: B,
    snippet: N,
  },
  // MANIFEST.json and REFUSED.json (lib/value-free-seal.mjs)
  manifest: {
    format: type(PUBLISH_FORMAT),
    status: type("published"),
    scanned: R("scanned"),
    files: L(R("manifest-file")),
  },
  scanned: { values: C },
  "manifest-file": { path: N, bytes: C, digest: H },
  refusal: {
    format: type(REFUSAL_FORMAT),
    status: type("refused"),
    reasons: L(R("reason")),
  },
  reason: (v) => (isCleanReason(v) ? { value: v } : { problem: UNKNOWN_FIELD }),
};

const isRecord = (v) =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const unknownKind = { problem: UNKNOWN_KIND };
const unknownField = { problem: UNKNOWN_FIELD };

/** `v` read as `spec`. `project`: the builder's reading, an open container projected to its empty value; else the scan's,
 * where a publication holds nothing but that empty value there. Returns { value } or { problem }. */
function read(v, spec, project) {
  if (v === null) return { value: null };
  if (typeof spec === "string")
    return KINDS[spec](v) ? { value: v } : unknownKind;
  if (spec.type)
    return typeof v === "string" && spec.type.includes(v)
      ? { value: v }
      : unknownKind;
  if (spec.list) {
    if (!Array.isArray(v)) return unknownKind;
    const out = [];
    for (const x of v) {
      const r = read(x, spec.list, project);
      if (r.problem) return r;
      out.push(r.value);
    }
    return { value: out };
  }
  if (spec.any) {
    for (const alternative of spec.any) {
      const r = read(v, alternative, project);
      if (!r.problem) return r;
    }
    return unknownKind;
  }
  if ("empty" in spec)
    return project || sameJson(v, spec.empty)
      ? { value: structuredClone(spec.empty) }
      : unknownField;
  const fields = RECORDS[spec.record];
  if (typeof fields === "function") return fields(v, project);
  if (!isRecord(v)) return unknownKind;
  const out = {};
  for (const [key, x] of Object.entries(v)) {
    // An unknown field refuses before its value is read at all.
    if (!Object.hasOwn(fields, key)) return unknownField;
    const r = read(x, fields[key], project);
    if (r.problem) return r;
    out[key] = r.value;
  }
  return { value: out };
}

function blobEvent(v, project) {
  if (!isRecord(v)) return unknownKind;
  if (Object.keys(v).some((k) => k !== "method" && k !== "params"))
    return unknownField;
  if (typeof v.method !== "string" || !Object.hasOwn(BLOB_EVENTS, v.method))
    return unknownKind;
  const params = BLOB_EVENTS[v.method];
  if (params === null)
    return v.params === undefined
      ? { value: { method: v.method } }
      : unknownField;
  if (v.params === undefined) return { value: { method: v.method } };
  const r = read(v.params, R(params), project);
  return r.problem ? r : { value: { method: v.method, params: r.value } };
}

/** The builder's reading of a record (lib/value-free-codec.ts): the record projected (each open container to its empty
 * value), or the refusal code when a field or a kind is not in its type's vocabulary. */
export function projectRecord(doc, recordType) {
  return read(doc, R(recordType), true);
}

/** The scan's reading of a published record (lib/value-free-reading.mjs): null when it holds only its type's vocabulary,
 * else the refusal code. */
export function checkRecord(doc, recordType) {
  return read(doc, R(recordType), false).problem ?? null;
}

/** S2: runner.log as a record (recordLayer): lines of text, read against checkLog. */
export const RUNNER_LOG = Object.freeze({ log: "runner-log" });

const EMBEDDED_ZIP_DATA = /data:application\/zip;base64,[A-Za-z0-9+/=]/;
const CLOSER = { "{": "}", "[": "]" };
/** Where the JSON object or array that opens at `start` closes (the index after it), or -1: its brackets matched outside
 * strings, and a string never across a line (JSON holds no raw newline in one), so a stray quote ends the search there. */
function closeOf(text, start) {
  const open = [];
  let inString = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inString) {
      if (c === "\\") i++;
      else if (c === '"') inString = false;
      else if (c === "\n") return -1;
    } else if (c === '"') inString = true;
    else if (c === "{" || c === "[") open.push(CLOSER[c]);
    else if (c === "}" || c === "]") {
      if (open.pop() !== c) return -1;
      if (open.length === 0) return i + 1;
    }
  }
  return -1;
}
const LEAD = /[^\S\n]*/y;
const parsed = (s) => {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
};
/** S2: the runner log's closed schema. Its one kind is a line of text. A document in it is not a line: a JSON object or
 * array that opens at the start of a line (after white space) and closes at the end of a line (that line or a later one, so
 * one line, a block of lines or the whole text), or an embedded zip. Returns null, or the refusal code (unknown-kind). A
 * fragment inside a line (`Error: API 400 on /x: {...}`) is text of that line, as it is in a message of any other record. */
export function checkLog(text) {
  if (EMBEDDED_ZIP_DATA.test(text)) return UNKNOWN_KIND;
  for (let at = 0; at < text.length; ) {
    const end = text.indexOf("\n", at);
    LEAD.lastIndex = at;
    const start = at + LEAD.exec(text)[0].length;
    if (text[start] === "{" || text[start] === "[") {
      const close = closeOf(text, start);
      if (close !== -1) {
        const next = text.indexOf("\n", close);
        const rest = text.slice(close, next === -1 ? text.length : next);
        const doc = /^\s*$/.test(rest)
          ? parsed(text.slice(start, close))
          : undefined;
        if (doc !== null && typeof doc === "object") return UNKNOWN_KIND;
      }
    }
    if (end === -1) break;
    at = end + 1;
  }
  return null;
}

/** What a published file is, by its path in the publication (any prefix above it, as a pull transfers it): { doc } a JSON
 * document of that record type; { zip } a zip whose entries entryLayer() classifies; { embedded } a text whose embedded
 * zips entryLayer() classifies; { log } the runner log (R2: lines of text, checkLog); { unknown: true } a report file of no
 * known kind; null a payload. */
export function recordLayer(rel) {
  const at = rel.split("\\").join("/");
  if (/(?:^|\/)reports\/json-\d+\.json$/.test(at))
    return { doc: "json-report" };
  if (/(?:^|\/)reports\/counts-\d+\.json$/.test(at))
    return { doc: "counts-report" };
  if (/(?:^|\/)reports\/blob-\d+\.zip$/.test(at)) return { zip: "blob" };
  if (/(?:^|\/)reports\/[^/]+$/.test(at)) return { unknown: true };
  if (/(?:^|\/)html(?:-\d+)?\/index\.html$/.test(at))
    return { embedded: "html" };
  if (/(?:^|\/)MANIFEST\.json$/.test(at)) return { doc: "manifest" };
  if (/(?:^|\/)REFUSED\.json$/.test(at)) return { doc: "refusal" };
  if (/(?:^|\/)runner\.log$/.test(at)) return RUNNER_LOG;
  return null;
}

/** An entry of a record zip (`layout`: blob, or html's embedded zip): { lines } JSON lines of that record type, { doc } one
 * record, null a payload, { unknown: true } an entry of no known kind. */
export function entryLayer(layout, name) {
  if (layout === "blob") {
    if (name === "report.jsonl") return { lines: "blob-event" };
    return /^resources\/[^/]+$/.test(name) ? null : { unknown: true };
  }
  if (layout === "html") {
    if (name === "report.json") return { doc: "html-report" };
    return /^[0-9a-f]{20}\.json$/.test(name)
      ? { doc: "html-file" }
      : { unknown: true };
  }
  return { unknown: true };
}
