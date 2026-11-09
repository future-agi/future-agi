/* eslint-env node */
// J07 binding checker: the one place that decides whether this J07 walk may run
// at all, and the cross-repo omega-embed/1 byte comparator (A28). Value-free by
// construction: it reads the runner's binding, the OSS artifact closure and the
// FA fixture, and emits only codes, counts and booleans.
//
// Used two ways:
//   - as the DERIVATION.json ordered invocations, before the first page:
//       node flows/omega/studio-embed.binding.cjs verify          --binding /run/j07/binding.json
//       node flows/omega/studio-embed.binding.cjs protocol-parity --binding /run/j07/binding.json
//   - from the spec's worker fixture (require), so a direct Playwright run refuses
//     the same way before any login, child start or connect token exists.
//
// Binding (schema j07-binding/v1). Every value is supplied by the Stage A runner;
// nothing here is guessed, and an unresolved LOCAL_/UNISSUED_/BOUND_/PENDING
// placeholder refuses dispatch:
//   {
//     "schema": "j07-binding/v1",
//     "faRef": "<40-hex FA commit under test>",
//     "origins": { "faApp": "http://localhost:3100", "faApi": "http://localhost:8100" },
//     "oss": {
//       "childSha": "<40-hex OSS commit the built omega dev child came from>",
//       "artifactRoot": "/opt/j07/oss",
//       "buildReceipt": "/opt/j07/oss/BUILD-RECEIPT.json",       // JSON naming sourceRef
//       "ancestry": [                                              // the runner's evidence, never assumed:
//         { "ancestor": "c103d224f0bc3460447a33bd169593ee2d375a19",  // one record per REQUIRED ancestor
//           "descendant": "<childSha>", "method": "git merge-base --is-ancestor", "exitCode": 0 },
//         { "ancestor": "79164e25f3f0223ddf76e0924785005601925335",
//           "descendant": "<childSha>", "method": "git merge-base --is-ancestor", "exitCode": 0 } ]
//     },
//     "studio": {
//       "node": "/abs/node", "cli": "packages/cli/dist/index.js",          // cli relative to artifactRoot
//       "projectTemplate": "packages/studio/fixtures/studio-fixture",       // relative to artifactRoot
//       "projectModules": "packages/studio/node_modules",                   // optional, relative
//       "workRoot": "/work/j07",
//       "wrapper": ["/abs/guarded-role-launcher", "..."],                   // the runner's role seam (argv prefix)
//       "port": 4191                                                        // optional; needed for J07-06b same-port restart
//     },
//     "seed": { "manifest": "/run/j07/seed.json" },        // j07-seed/v1: per-engine org A admitted / org B denied, 2 users each
//     "observer": { "install": "e4" },                     // 'e4': the shared runner's hook installs it; 'direct': this consumer does
//     "appSide": { "descriptor": "/run/j07/APP-SIDE-EFFECTIVE.json" },   // cj-app-side/*, sourceRef === faRef, read at admission
//     "logs": { "roles": {                                 // the COMPLETE log set of the walk window, every role declared
//       "fa-frontend": ["/run/j07/logs/fa-frontend.log"], "fa-access": ["/run/j07/logs/fa-access.log"],
//       "fa-api": ["/run/j07/logs/fa-api.log"], "studio-access": ["/run/j07/logs/studio-access.log"] } },
//                                                       // studio-access: one aggregate file or one per child; J07-07
//                                                       // requires EVERY child's own start/end markers in a declared
//                                                       // file (a child whose log is not declared: BLOCKED)
//     "g5a": { "receipt": "/run/j07/G5A-RECEIPT.json", "receiptSha256": "<64-hex>" },   // G5a producer qualification
//     "browsers": { "webkit": { "qualified": false, "successor": "wf_e92197a0-c1b" } },
//     "reporters": { "accepted": [{ "module": "/abs/reporter.cjs", "sha256": "<64-hex>" }] }
//   }
//
// G5a receipt (schema j07-g5a-qualification/v1), the runner's qualification of the
// exact failure-artifact producer installed beside this spec:
//   { "schema": "j07-g5a-qualification/v1", "verdict": "QUALIFIED",
//     "producer": { "package": "playwright", "version": "1.62.1",
//                   "files": { "lib/index.js": "<sha256>", "lib/errorContext.js": "<sha256>" } },
//     "engines": ["chromium", "firefox"],
//     "paths": { "success": "pass", "assertion-failure": "pass", "goto-timeout": "pass",
//                "setup-failure": "pass", "test-timeout": "pass", "worker-crash": "pass" },
//     "canaryOccurrences": 0, "captureFiles": 0 }
// Absent or incomplete evidence refuses G5A_UNQUALIFIED before any login or token;
// evidence for any other producer bytes refuses G5A_PRODUCER_MISMATCH.
//
// The child contract this binding admits: `omega dev --no-open --embed-origin <faApp>`
// run with FI_APP_URL, FI_API_KEY and FI_SECRET_KEY unset, stdout a pipe (not a
// TTY), from an OSS build whose source descends from BOTH
//   - c103d224f0 (a framed Studio keeps the key its host hands over in memory only), and
//   - 79164e25f3 (release-lead A27 ruling (b): a listing that is not a terminal, while
//     pages may frame Studio, prints the local link as the literal `Open <url>/#t=<key>`;
//     the key is printed once, only in the "Open in Future AGI" link's fragment).
// Ancestry evidence missing for c103d224f0 refuses OSS_LACKS_MEMORY_ONLY_KEY; for
// 79164e25f3, OSS_LACKS_KEYLESS_LISTING. Either way the child is refused here,
// value-free, before it is ever started.
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const SCHEMA = "j07-binding/v1";
const SEED_SCHEMA = "j07-seed/v1";
/** OSS fix(studio-ui): a framed Studio keeps the key its host hands over in memory only. */
const REQUIRED_OSS_ANCESTOR = "c103d224f0bc3460447a33bd169593ee2d375a19";
/**
 * OSS fix(dev) (research/studio-embed-log-key-1031, red f790f2c2e8): while pages may frame
 * Studio, a listing that is not a terminal leaves the key out of the local link (A27 ruling (b)).
 */
const REQUIRED_OSS_KEYLESS_LISTING = "79164e25f3f0223ddf76e0924785005601925335";
/** Every ancestor the OSS child SHA must descend from, each with its own value-free refusal. */
const REQUIRED_OSS_ANCESTORS = Object.freeze([
  Object.freeze({ sha: REQUIRED_OSS_ANCESTOR, code: "OSS_LACKS_MEMORY_ONLY_KEY" }),
  Object.freeze({ sha: REQUIRED_OSS_KEYLESS_LISTING, code: "OSS_LACKS_KEYLESS_LISTING" }),
]);
/** The nominated canonical omega-embed/1 fixture (957 bytes incl. newline). */
const CANONICAL_FIXTURE = Object.freeze({
  bytes: 957,
  sha256: "b2c0ed231414ffa35f07213a29702f411f514d130ded8d0c9d189e91afa82438",
});
const FA_FIXTURE = "frontend/src/sections/omega/embed/omega-embed-1.json";
const OSS_FIXTURE = "packages/studio-protocol/openapi/omega-embed-1.json";
const WEBKIT_SUCCESSOR = "wf_e92197a0-c1b";
const ANCESTRY_METHOD = "git merge-base --is-ancestor";

const SHA40 = /^[0-9a-f]{40}$/;
const SHA64 = /^[0-9a-f]{64}$/;
const E2E_DIR = path.resolve(__dirname, "..", "..");
const J07_REPORTER = path.join(__dirname, "studio-embed.reporter.cjs");
const INSTALL_MODES = new Set(["e4", "direct"]);
const LOG_ROLES = Object.freeze(["fa-frontend", "fa-access", "fa-api", "studio-access"]);
const G5A_SCHEMA = "j07-g5a-qualification/v1";
const G5A_PATHS = Object.freeze(["success", "assertion-failure", "goto-timeout", "setup-failure", "test-timeout", "worker-crash"]);
/** The Playwright 1.62.1 failure-artifact producer: ArtifactsRecorder (lib/index.js) and buildErrorContext. */
const G5A_REQUIRED_FILES = Object.freeze(["lib/index.js", "lib/errorContext.js"]);
const BUILTIN_REPORTERS = new Set(["list", "line", "dot", "json", "junit", "null", "github", "html", "blob"]);
const PLACEHOLDER = /(^|[^A-Za-z])(LOCAL_[A-Z_]+|UNISSUED_[A-Z_]*|BOUND_[A-Z_]+|PENDING)\b|<[^>]*>/;
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** A constant, value-free refusal. */
class BindingRefusal extends Error {
  constructor(code) {
    super(`J07-BLOCKED ${code}`);
    this.name = "J07Blocked";
    this.code = code;
  }
}

const refuse = (code) => {
  throw new BindingRefusal(code);
};

function isLoopbackOrigin(value) {
  try {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      LOOPBACK_HOSTS.has(url.hostname) &&
      url.origin === value
    );
  } catch {
    return false;
  }
}

function readJson(file, code) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return refuse(code);
  }
  try {
    return JSON.parse(text);
  } catch {
    return refuse(code);
  }
}

function strings(value, out = []) {
  if (typeof value === "string") out.push(value);
  else if (Array.isArray(value)) value.forEach((item) => strings(item, out));
  else if (value && typeof value === "object")
    Object.values(value).forEach((item) => strings(item, out));
  return out;
}

const isAbsFile = (file) =>
  typeof file === "string" && path.isAbsolute(file) && fs.existsSync(file);

/** A path inside `root` that is a regular file and not a symlink, resolving inside root. */
function contained(root, relative, code) {
  if (typeof relative !== "string" || relative === "" || path.isAbsolute(relative))
    refuse(code);
  const realRoot = fs.realpathSync(root);
  const candidate = path.resolve(realRoot, relative);
  let stat;
  try {
    stat = fs.lstatSync(candidate);
  } catch {
    return refuse(code);
  }
  if (stat.isSymbolicLink()) refuse(code);
  const real = fs.realpathSync(candidate);
  if (real !== candidate || !(real === realRoot || real.startsWith(realRoot + path.sep)))
    refuse(code);
  return real;
}

/**
 * Loads and validates the binding. Throws BindingRefusal (message "J07-BLOCKED <CODE>")
 * on anything missing, unbound or unproven. Returns the binding plus resolved paths.
 */
function loadBinding(file, options = {}) {
  const e2eDir = options.e2eDir ?? E2E_DIR;
  if (typeof file !== "string" || file === "") refuse("BINDING_MISSING");
  if (!path.isAbsolute(file) || !fs.existsSync(file)) refuse("BINDING_MISSING");
  const binding = readJson(file, "BINDING_UNREADABLE");
  if (!binding || binding.schema !== SCHEMA) refuse("BINDING_SCHEMA");
  if (strings(binding).some((value) => PLACEHOLDER.test(value)))
    refuse("BINDING_PLACEHOLDER");
  if (!SHA40.test(String(binding.faRef ?? ""))) refuse("FA_REF");

  const origins = binding.origins ?? {};
  if (!isLoopbackOrigin(origins.faApp) || !isLoopbackOrigin(origins.faApi))
    refuse("ORIGIN_NOT_LOOPBACK");

  const oss = binding.oss ?? {};
  if (!SHA40.test(String(oss.childSha ?? ""))) refuse("OSS_CHILD_SHA");
  // Ancestry for every required OSS commit is evidence the runner supplies; never assumed.
  const ancestry = ancestryEvidence(oss);
  if (typeof oss.artifactRoot !== "string" || !path.isAbsolute(oss.artifactRoot))
    refuse("OSS_ARTIFACT");
  if (!fs.existsSync(oss.artifactRoot) || !fs.statSync(oss.artifactRoot).isDirectory())
    refuse("OSS_ARTIFACT");
  // The built artifact must name the same source the ancestry evidence names.
  if (!isAbsFile(oss.buildReceipt)) refuse("OSS_RECEIPT");
  const receipt = readJson(oss.buildReceipt, "OSS_RECEIPT");
  if (!receipt || receipt.sourceRef !== oss.childSha) refuse("OSS_RECEIPT_SOURCE");

  const studio = binding.studio ?? {};
  if (!isAbsFile(studio.node)) refuse("STUDIO_NODE");
  const cli = contained(oss.artifactRoot, studio.cli, "STUDIO_CLI");
  const projectTemplate = (() => {
    if (typeof studio.projectTemplate !== "string") return refuse("STUDIO_PROJECT");
    const dir = path.resolve(fs.realpathSync(oss.artifactRoot), studio.projectTemplate);
    if (!dir.startsWith(fs.realpathSync(oss.artifactRoot) + path.sep)) refuse("STUDIO_PROJECT");
    if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) refuse("STUDIO_PROJECT");
    return dir;
  })();
  let projectModules = null;
  if (studio.projectModules !== undefined) {
    const dir = path.resolve(fs.realpathSync(oss.artifactRoot), studio.projectModules);
    if (!dir.startsWith(fs.realpathSync(oss.artifactRoot) + path.sep) || !fs.existsSync(dir))
      refuse("STUDIO_MODULES");
    projectModules = dir;
  }
  if (typeof studio.workRoot !== "string" || !path.isAbsolute(studio.workRoot))
    refuse("STUDIO_WORKROOT");
  // The guarded role seam: the runner's launcher records the child's lifecycle.
  // No wrapper, no child: this spec never starts an unguarded omega dev.
  if (
    !Array.isArray(studio.wrapper) ||
    studio.wrapper.length === 0 ||
    !studio.wrapper.every((arg) => typeof arg === "string" && arg !== "") ||
    !isAbsFile(studio.wrapper[0])
  )
    refuse("STUDIO_ROLE_SEAM");
  if (
    studio.port !== undefined &&
    !(Number.isInteger(studio.port) && studio.port > 1024 && studio.port < 65536)
  )
    refuse("STUDIO_PORT");

  if (!isAbsFile(binding.seed?.manifest)) refuse("SEED_MANIFEST");

  // E4 observer path: declared, never inferred (acquireCensus enforces it per context).
  const observerInstall = binding.observer?.install;
  if (!INSTALL_MODES.has(observerInstall)) refuse("OBSERVER_INSTALL_MODE");

  // Pre-workload: the effective app-side descriptor of THIS FA build, inert and local.
  const appSide = appSideFacts(binding);
  // The complete declared log set the A26/A27 audit covers; an omitted role refuses.
  const logs = logInventory(binding);
  // G5a: the failure-artifact producer installed beside this spec is the qualified one.
  const g5a = g5aQualification(binding, e2eDir);
  const accepted = acceptedReporters(binding);

  const webkit = binding.browsers?.webkit ?? {};
  return {
    binding,
    resolved: {
      cli,
      projectTemplate,
      projectModules,
      webkitQualified: webkit.qualified === true && webkit.successor === WEBKIT_SUCCESSOR,
      ancestry,
      observerInstall,
      appSide,
      logs,
      g5a,
      accepted,
    },
  };
}

const isObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);

/**
 * The runner's ancestry evidence: one `git merge-base --is-ancestor <ancestor> <childSha>`
 * record (exit code 0) per REQUIRED ancestor. A single legacy record is read as a list of
 * one. An unknown or duplicated ancestor refuses; a required ancestor with no record, or
 * whose record says it is not an ancestor, refuses with that ancestor's own code.
 */
function ancestryEvidence(oss) {
  const raw = oss.ancestry;
  if (raw === undefined || raw === null) refuse("OSS_ANCESTRY_MISSING");
  const records = Array.isArray(raw) ? raw : [raw];
  if (records.length === 0 || !records.every(isObject)) refuse("OSS_ANCESTRY_MISSING");
  const required = new Set(REQUIRED_OSS_ANCESTORS.map((item) => item.sha));
  const seen = new Set();
  for (const record of records) {
    if (!required.has(record.ancestor)) refuse("OSS_ANCESTRY_WRONG_ANCESTOR");
    if (seen.has(record.ancestor)) refuse("OSS_ANCESTRY_DUPLICATE");
    seen.add(record.ancestor);
  }
  for (const { sha, code } of REQUIRED_OSS_ANCESTORS) {
    const record = records.find((item) => item.ancestor === sha);
    if (!record) refuse(code);
    if (record.descendant !== oss.childSha) refuse("OSS_ANCESTRY_WRONG_DESCENDANT");
    if (record.method !== ANCESTRY_METHOD) refuse("OSS_ANCESTRY_METHOD");
    if (record.exitCode !== 0) refuse(code);
  }
  return { required: REQUIRED_OSS_ANCESTORS.length, proven: REQUIRED_OSS_ANCESTORS.length };
}

function isRegularAbsFile(file) {
  if (typeof file !== "string" || !path.isAbsolute(file)) return false;
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

/**
 * The bound effective build/runtime descriptor (CJ-OVERLAY APP-SIDE), read before
 * any workload: schema, sourceRef equal to the bound FA ref, both environments
 * present, every required telemetry key declared, every telemetry key inert and
 * every URL loopback. Anything else refuses value-free.
 */
function appSideFacts(binding) {
  const file = binding.appSide?.descriptor;
  if (!isRegularAbsFile(file)) refuse("APP_SIDE_DESCRIPTOR");
  const descriptor = readJson(file, "APP_SIDE_DESCRIPTOR");
  if (!isObject(descriptor) || typeof descriptor.schema !== "string" || !/^cj-app-side\/v\d+$/.test(descriptor.schema))
    refuse("APP_SIDE_SCHEMA");
  if (descriptor.sourceRef !== binding.faRef) refuse("APP_SIDE_SOURCE_REF");
  if (!isObject(descriptor.buildEnvironment) || !isObject(descriptor.runtimeEnvironment)) refuse("APP_SIDE_INCOMPLETE");
  const facts = inertness(descriptor, true);
  if (facts.missingRequired > 0) refuse("APP_SIDE_INCOMPLETE");
  if (facts.telemetryLive > 0) refuse("APP_SIDE_TELEMETRY_LIVE");
  if (facts.nonlocalUrls > 0) refuse("APP_SIDE_NONLOCAL");
  return { sourceRefMatches: true, ...facts };
}

/** Every declared log role, each with at least one absolute regular file; unknown roles refuse. */
function logInventory(binding) {
  const roles = binding.logs?.roles;
  if (!isObject(roles)) refuse("LOG_INVENTORY");
  for (const role of Object.keys(roles)) if (!LOG_ROLES.includes(role)) refuse("LOG_ROLE_UNKNOWN");
  const out = {};
  for (const role of LOG_ROLES) {
    const files = roles[role];
    const code = `LOG_ROLE_UNDECLARED_${role.toUpperCase().replace(/-/g, "_")}`;
    if (!Array.isArray(files) || files.length === 0) refuse(code);
    if (!files.every(isRegularAbsFile)) refuse("LOG_FILE");
    if (new Set(files).size !== files.length) refuse("LOG_FILE");
    out[role] = files.slice();
  }
  return out;
}

/** The Playwright package this spec actually runs with, resolved from the e2e directory. */
function installedProducer(e2eDir) {
  let packageFile;
  try {
    packageFile = require.resolve("playwright/package.json", { paths: [e2eDir] });
  } catch {
    return null;
  }
  let version = null;
  let testVersion = null;
  try {
    version = JSON.parse(fs.readFileSync(packageFile, "utf8")).version ?? null;
  } catch {
    return null;
  }
  try {
    testVersion = JSON.parse(fs.readFileSync(require.resolve("@playwright/test/package.json", { paths: [e2eDir] }), "utf8")).version ?? null;
  } catch {
    testVersion = null;
  }
  return { package: "playwright", version, testVersion, dir: path.dirname(packageFile) };
}

/**
 * G5a admission: a runner receipt, bound by sha256 in the binding, that qualifies
 * the exact installed producer bytes on every failure path. Refuses before any
 * login, child start or token.
 */
function g5aQualification(binding, e2eDir) {
  const g5a = binding.g5a;
  if (!isObject(g5a) || !isRegularAbsFile(g5a.receipt) || !SHA64.test(String(g5a.receiptSha256 ?? "")))
    refuse("G5A_UNQUALIFIED");
  let bytes;
  try {
    bytes = fs.readFileSync(g5a.receipt);
  } catch {
    return refuse("G5A_RECEIPT");
  }
  if (sha256(bytes) !== g5a.receiptSha256) refuse("G5A_RECEIPT");
  let receipt;
  try {
    receipt = JSON.parse(bytes.toString("utf8"));
  } catch {
    return refuse("G5A_RECEIPT");
  }
  if (!isObject(receipt) || receipt.schema !== G5A_SCHEMA || receipt.verdict !== "QUALIFIED") refuse("G5A_UNQUALIFIED");
  if (!isObject(receipt.paths) || !G5A_PATHS.every((name) => receipt.paths[name] === "pass")) refuse("G5A_UNQUALIFIED");
  if (receipt.canaryOccurrences !== 0 || receipt.captureFiles !== 0) refuse("G5A_UNQUALIFIED");
  const engines = receipt.engines;
  if (!Array.isArray(engines) || engines.length === 0 || !engines.every((name) => ["chromium", "firefox", "webkit"].includes(name)))
    refuse("G5A_UNQUALIFIED");
  const installed = installedProducer(e2eDir);
  if (!installed || typeof installed.version !== "string") refuse("G5A_PRODUCER_UNRESOLVED");
  const producer = isObject(receipt.producer) ? receipt.producer : {};
  if (producer.package !== installed.package || producer.version !== installed.version || installed.testVersion !== installed.version)
    refuse("G5A_PRODUCER_MISMATCH");
  const files = isObject(producer.files) ? producer.files : {};
  if (!G5A_REQUIRED_FILES.every((name) => typeof files[name] === "string")) refuse("G5A_PRODUCER_MISMATCH");
  for (const [relative, digest] of Object.entries(files)) {
    if (!SHA64.test(String(digest))) refuse("G5A_PRODUCER_MISMATCH");
    const real = contained(installed.dir, relative, "G5A_PRODUCER_MISMATCH");
    if (sha256(fs.readFileSync(real)) !== digest) refuse("G5A_PRODUCER_MISMATCH");
  }
  return { qualified: true, version: installed.version, engines: engines.slice(), files: Object.keys(files).length };
}

/** Extra reporters the runner qualified for this invocation, by exact module bytes. */
function acceptedReporters(binding) {
  const list = binding.reporters?.accepted ?? [];
  if (!Array.isArray(list)) refuse("REPORTERS_ACCEPTED");
  return list.map((entry) => {
    if (!isObject(entry) || !isRegularAbsFile(entry.module) || !SHA64.test(String(entry.sha256 ?? ""))) refuse("REPORTERS_ACCEPTED");
    return { real: fs.realpathSync(entry.module), sha256: entry.sha256 };
  });
}

/**
 * The per-engine seed: org A admitted to studio_tab, org B denied, two synthetic
 * users each. Credentials stay in memory; nothing here is ever emitted.
 */
function loadSeed(binding, browserName) {
  const seed = readJson(binding.seed.manifest, "SEED_MANIFEST");
  if (!seed || seed.schema !== SEED_SCHEMA) refuse("SEED_SCHEMA");
  const engine = seed.engines?.[browserName];
  if (!engine) refuse("SEED_ENGINE");
  const org = (key, admission) => {
    const value = engine.orgs?.[key];
    if (!value || typeof value.id !== "string" || value.id === "") refuse("SEED_ORG");
    if (value.studioTab !== admission) refuse("SEED_ADMISSION");
    if (!Array.isArray(value.users) || value.users.length !== 2) refuse("SEED_USERS");
    for (const user of value.users) {
      if (typeof user?.email !== "string" || typeof user?.password !== "string")
        refuse("SEED_USERS");
      if (user.email === "" || user.password === "") refuse("SEED_USERS");
    }
    return { id: value.id, users: value.users.map((u) => ({ email: u.email, password: u.password })) };
  };
  const A = org("A", "admitted");
  const B = org("B", "denied");
  if (A.id === B.id) refuse("SEED_ORG");
  return { A, B };
}

const sha256 = (buffer) => crypto.createHash("sha256").update(buffer).digest("hex");

/**
 * A28: the FA and OSS omega-embed-1.json as raw bytes, compared exactly, each
 * also against the canonical nomination. C09 in-run fallibility: a one-byte,
 * semantically equivalent corruption of the FA bytes must fail the comparator
 * while still parsing to the same JSON. Emits only counts and booleans.
 */
function protocolParity(binding, faRoot) {
  const faPath = contained(faRoot, FA_FIXTURE, "FA_FIXTURE");
  const ossPath = contained(binding.oss.artifactRoot, OSS_FIXTURE, "OSS_FIXTURE");
  const fa = fs.readFileSync(faPath);
  const oss = fs.readFileSync(ossPath);
  const faCanonical = fa.length === CANONICAL_FIXTURE.bytes && sha256(fa) === CANONICAL_FIXTURE.sha256;
  const ossCanonical = oss.length === CANONICAL_FIXTURE.bytes && sha256(oss) === CANONICAL_FIXTURE.sha256;
  const equal = Buffer.compare(fa, oss) === 0;
  // One-byte control: an indentation space becomes a tab (still the same JSON).
  const corrupt = Buffer.from(fa);
  const at = corrupt.indexOf(Buffer.from("\n  "));
  let controlDetected = false;
  let controlSemanticallyEqual = false;
  if (at >= 0) {
    corrupt[at + 1] = 0x09;
    controlDetected = Buffer.compare(corrupt, oss) !== 0 && !corrupt.equals(fa);
    try {
      controlSemanticallyEqual =
        JSON.stringify(JSON.parse(corrupt.toString("utf8"))) ===
        JSON.stringify(JSON.parse(fa.toString("utf8")));
    } catch {
      controlSemanticallyEqual = false;
    }
  }
  return {
    faBytes: fa.length,
    ossBytes: oss.length,
    faCanonical,
    ossCanonical,
    equal,
    controlDetected,
    controlSemanticallyEqual,
    pass: faCanonical && ossCanonical && equal && controlDetected && controlSemanticallyEqual,
  };
}

const realpathOrNull = (file) => {
  try {
    return fs.realpathSync(file);
  } catch {
    return null;
  }
};
const sha256File = (file) => {
  try {
    return sha256(fs.readFileSync(file));
  } catch {
    return null;
  }
};

/**
 * Reporter ALLOWLIST (A37), checked at worker admission before any login or token:
 * the built-in 'line' reporter, exactly one J07 value-free reporter (this
 * directory's studio-embed.reporter.cjs by real path), and modules the runner
 * qualified for this invocation by real path AND sha256. Every other reporter,
 * built-in or custom, is refused; a filename suffix is never identity.
 *   names: config.reporter ids as Playwright resolved them (built-in name or absolute path)
 */
function reporterViolations(names, accepted = []) {
  const out = [];
  const j07 = realpathOrNull(J07_REPORTER);
  let j07Count = 0;
  for (const raw of names) {
    const name = String(raw);
    if (name === "line") continue;
    if (BUILTIN_REPORTERS.has(name)) {
      out.push("REPORTER_NOT_ALLOWLISTED");
      continue;
    }
    const real = path.isAbsolute(name) ? realpathOrNull(name) : null;
    if (real === null) {
      out.push("REPORTER_UNRESOLVED");
      continue;
    }
    if (j07 !== null && real === j07) {
      j07Count += 1;
      continue;
    }
    const entry = accepted.find((item) => item.real === real);
    if (entry && sha256File(real) === entry.sha256) continue;
    out.push("REPORTER_NOT_ALLOWLISTED");
  }
  if (j07Count !== 1) out.push("REPORTER_J07_ABSENT");
  return out;
}

// ---------------------------------------------------------------- debug logging (A37)
//
// DEBUG is read by the `debug` module Playwright 1.62.1 bundles (playwright-core
// lib/utilsBundle.js, node_modules/debug/src/common.js): enable() trims, turns every
// whitespace run into a comma, splits on commas, drops empties, and treats a leading
// '-' as a skip; enabled(name) refuses on any matching skip, then admits on any
// matching name; a template matches with '*' as the only wildcard. Transcribed
// verbatim below, so admission answers exactly what the worker's loggers will do.

function debugMatchesTemplate(search, template) {
  let searchIndex = 0;
  let templateIndex = 0;
  let starIndex = -1;
  let matchIndex = 0;
  while (searchIndex < search.length) {
    if (templateIndex < template.length && (template[templateIndex] === search[searchIndex] || template[templateIndex] === "*")) {
      if (template[templateIndex] === "*") {
        starIndex = templateIndex;
        matchIndex = searchIndex;
        templateIndex++;
      } else {
        searchIndex++;
        templateIndex++;
      }
    } else if (starIndex !== -1) {
      templateIndex = starIndex + 1;
      matchIndex++;
      searchIndex = matchIndex;
    } else {
      return false;
    }
  }
  while (templateIndex < template.length && template[templateIndex] === "*") templateIndex++;
  return templateIndex === template.length;
}

/** `debug`'s enable(namespaces) + enabled(name), as Playwright 1.62.1 bundles it. */
function debugEnabled(namespaces) {
  const names = [];
  const skips = [];
  const split = (typeof namespaces === "string" ? namespaces : "").trim().replace(/\s+/g, ",").split(",").filter(Boolean);
  for (const ns of split) {
    if (ns[0] === "-") skips.push(ns.slice(1));
    else names.push(ns);
  }
  return (name) => {
    for (const skip of skips) if (debugMatchesTemplate(name, skip)) return false;
    for (const ns of names) if (debugMatchesTemplate(name, ns)) return true;
    return false;
  };
}

/**
 * Node's util.debuglog over NODE_DEBUG (lib/internal/util/debuglog.js): regexp
 * metacharacters escaped, '*' becomes '.*', ',' separates (no trimming, no
 * negation), anchored and CASE-INSENSITIVE. Verified against node 26: 'HTTP'
 * enables http, 'ht*' enables http and http2, ' http' enables nothing.
 */
function nodeDebugEnabled(value) {
  if (typeof value !== "string" || value === "") return () => false;
  const source = value.replace(/[|\\{}()[\]^$+?.]/g, "\\$&").replace(/\*/g, ".*").replace(/,/g, "$|^");
  let re;
  try {
    re = new RegExp(`^${source}$`, "i");
  } catch {
    return () => true; // unparseable: assume it enables everything
  }
  return (name) => re.test(name);
}

/**
 * Every namespace a logger in the Playwright 1.62.1 test worker or runner can write
 * through `debug` (debugLogger.log names, the pw:test/mcp/tools/android debuggers, and
 * the bundled proxy agents). Each can carry a URL, header, request or runner message.
 */
const DEBUG_SENSITIVE = Object.freeze([
  "pw:api", "pw:protocol", "pw:channel", "pw:server", "pw:server:channel", "pw:server:metadata", "pw:browser",
  "pw:error", "pw:socks", "pw:client-certificates", "pw:install", "pw:download", "pw:recorder", "pw:webserver",
  "pw:test", "pw:test:protocol", "pw:test:task", "pw:tools:error", "pw:android", "pw:adb", "pw:adb:data",
  "pw:adb:recv", "pw:adb:send", "pw:mcp:error", "pw:mcp:file", "pw:mcp:relay", "pw:mcp:request", "pw:mcp:server",
  "pw:mcp:server:response", "pw:mcp:test",
  "https-proxy-agent", "https-proxy-agent:parse-proxy-response", "socks-proxy-agent",
]);
/** Node debuglog sections that can write a URL, header, socket or stream chunk (the child's stdout). */
const NODE_DEBUG_SENSITIVE = Object.freeze([
  "http", "http2", "https", "net", "tls", "dns", "fetch", "undici", "websocket", "stream", "child_process",
  "worker", "esm", "module",
]);
/** Probes no logger names: enabled only through a wildcard (pw:*, *, p*, ...). */
const DEBUG_PROBES = Object.freeze(["pw:j07-wildcard-probe", "pw:j07-wildcard-probe:inner", "j07-wildcard-probe"]);
const NODE_DEBUG_PROBES = Object.freeze(["j07_wildcard_probe"]);

/**
 * A37: refuses every debug-logging selector that can write protocol, network, header,
 * runner or child-stream content, interpreted with the exact semantics of the loggers
 * that read it. Returns violation codes (empty: nothing is enabled that could log).
 */
function debugLoggingViolations(env = {}) {
  const out = [];
  if (env.PWDEBUG) out.push("PWDEBUG");
  if (env.PWDEBUGIMPL) out.push("PWDEBUGIMPL");
  const debug = debugEnabled(env.DEBUG);
  if (DEBUG_SENSITIVE.some((name) => name.startsWith("pw:") && debug(name)) || DEBUG_PROBES.slice(0, 2).some(debug))
    out.push("DEBUG_PW");
  if (DEBUG_SENSITIVE.some((name) => !name.startsWith("pw:") && debug(name))) out.push("DEBUG_PROXY_AGENT");
  if (debug(DEBUG_PROBES[2])) out.push("DEBUG_WILDCARD");
  const node = nodeDebugEnabled(env.NODE_DEBUG);
  if (NODE_DEBUG_SENSITIVE.some(node) || NODE_DEBUG_PROBES.some(node)) out.push("NODE_DEBUG_NET");
  // The browsers read these themselves: Firefox's MOZ_LOG/NSPR modules log HTTP headers.
  if (env.MOZ_LOG || env.NSPR_LOG_MODULES) out.push("BROWSER_LOG_ENV");
  return out;
}

// ---------------------------------------------------------------- effective capture (A37)

const CAPTURE_OFF = new Set([undefined, null, "off", false]);
const modeOf = (value) => (value && typeof value === "object" ? value.mode : value);
/** Chromium switches that write URLs/headers (NetLog, verbose logging) or expose the protocol. */
const BROWSER_LOGGING_ARG = /^--(?:log-net-log|net-log-capture-mode|enable-logging|v|vmodule|remote-debugging-port|remote-debugging-pipe|remote-debugging-address|auto-open-devtools-for-tabs|dump-dom)(?:=|$)/;

/** Capture a set of context options would make: HAR, video, storage state, a proxy. */
function contextOptionViolations(options, out = []) {
  if (!isObject(options)) return out;
  if (options.recordHar !== undefined && options.recordHar !== null) out.push("RECORD_HAR");
  if (options.recordVideo !== undefined && options.recordVideo !== null) out.push("RECORD_VIDEO");
  if (options.storageState !== undefined && options.storageState !== null) out.push("STORAGE_STATE");
  if (options.proxy !== undefined && options.proxy !== null) out.push("PROXY");
  return out;
}

/**
 * Worker-scoped EFFECTIVE values, as the fixture pool resolved them for this worker
 * (config use, project use, CLI overrides and any test.use, merged): the trace,
 * screenshot and video modes Playwright itself reads, the launch options the browser
 * starts with, and a remote browser connection.
 *   effective: { trace, screenshot, video, launchOptions, connectOptions }
 */
function effectiveWorkerViolations(effective) {
  if (!isObject(effective) || !("trace" in effective) || !("screenshot" in effective) || !("video" in effective))
    return ["EFFECTIVE_UNRESOLVED"];
  const out = [];
  if (!CAPTURE_OFF.has(modeOf(effective.trace))) out.push("TRACE_ON");
  if (!CAPTURE_OFF.has(modeOf(effective.screenshot))) out.push("SCREENSHOT_ON");
  if (!CAPTURE_OFF.has(modeOf(effective.video))) out.push("VIDEO_ON");
  if (effective.connectOptions !== undefined && effective.connectOptions !== null) out.push("CONNECT_OPTIONS");
  const launch = isObject(effective.launchOptions) ? effective.launchOptions : {};
  if (launch.logger !== undefined) out.push("LAUNCH_LOGGER");
  if (launch.proxy !== undefined && launch.proxy !== null) out.push("PROXY");
  if (launch.devtools === true) out.push("DEVTOOLS");
  if (Array.isArray(launch.args) && launch.args.some((arg) => BROWSER_LOGGING_ARG.test(String(arg)))) out.push("BROWSER_LOGGING");
  const prefs = isObject(launch.firefoxUserPrefs) ? Object.keys(launch.firefoxUserPrefs) : [];
  if (prefs.some((key) => /^logging\./.test(key) || /^network\.http\.log/.test(key))) out.push("BROWSER_LOGGING");
  if (isObject(launch.env) && debugLoggingViolations(launch.env).length > 0) out.push("BROWSER_LOG_ENV");
  return out;
}

/**
 * Test-scoped EFFECTIVE context options: Playwright's merged `_combinedContextOptions`
 * (every option fixture over `contextOptions`), which `_setupArtifacts`
 * (runBeforeCreateBrowserContext) copies into EVERY browser.newContext the test makes,
 * manual contexts included, plus the public `contextOptions` and `storageState`.
 *   effective: { combinedContextOptions, contextOptions, storageState }
 */
function effectiveContextViolations(effective) {
  if (!isObject(effective) || !isObject(effective.combinedContextOptions) || !("contextOptions" in effective))
    return ["EFFECTIVE_UNRESOLVED"];
  const out = [];
  contextOptionViolations(effective.combinedContextOptions, out);
  contextOptionViolations(effective.contextOptions, out);
  if (effective.storageState !== undefined && effective.storageState !== null) out.push("STORAGE_STATE");
  return [...new Set(out)];
}

/**
 * A37 / C13-config-trace: refuses any resolved configuration that could capture
 * the page, a header or storage before a reporter runs. Returns violation codes.
 *   resolved: { use, workers, retries, reporters: string[], env, accepted?, effective? }
 * `use` is the project use (config use merged); `effective` the worker-scoped values the
 * fixture pool actually resolved, which a test.use or CLI override can change.
 */
function capturePolicyViolations(resolved) {
  const out = [];
  const use = resolved.use ?? {};
  if (!CAPTURE_OFF.has(modeOf(use.trace))) out.push("TRACE_ON");
  if (!CAPTURE_OFF.has(modeOf(use.screenshot))) out.push("SCREENSHOT_ON");
  if (!CAPTURE_OFF.has(modeOf(use.video))) out.push("VIDEO_ON");
  contextOptionViolations(use, out);
  contextOptionViolations(use.contextOptions, out);
  if (resolved.effective !== undefined) out.push(...effectiveWorkerViolations(resolved.effective));
  if (resolved.workers !== 1) out.push("WORKERS");
  if (resolved.retries !== 0) out.push("RETRIES");
  const env = resolved.env ?? {};
  out.push(...debugLoggingViolations(env));
  if (env.E2E_RECORD_VIDEO === "1") out.push("E2E_RECORD_VIDEO");
  if (env.PW_TEST_CONNECT_WS_ENDPOINT) out.push("CONNECT_OPTIONS");
  // Playwright 1.62.1 (lib/index.js ArtifactsRecorder._takePageSnapshot) captures an
  // ARIA page snapshot into error-context.md unless this is set.
  if (env.PLAYWRIGHT_NO_COPY_PROMPT !== "1") out.push("ARIA_SNAPSHOT_ON");
  out.push(...reporterViolations(resolved.reporters ?? [], resolved.accepted ?? []));
  return [...new Set(out)];
}

/** C13-config-trace in-run fallibility: every unsafe variant must be refused. */
function capturePolicySelfTest() {
  const effective = { trace: "off", screenshot: "off", video: "off", launchOptions: {}, connectOptions: undefined };
  const safe = {
    use: { trace: "off", screenshot: "off", video: "off" },
    workers: 1,
    retries: 0,
    reporters: ["line", J07_REPORTER],
    env: { PLAYWRIGHT_NO_COPY_PROMPT: "1" },
    accepted: [],
    effective,
  };
  const variants = [
    { use: { ...safe.use, trace: "retain-on-failure" } },
    { use: { ...safe.use, screenshot: "only-on-failure" } },
    { use: { ...safe.use, video: "on" } },
    { use: { ...safe.use, recordHar: { path: "x.har" } } },
    { use: { ...safe.use, trace: { mode: "on" } } },
    { use: { ...safe.use, contextOptions: { recordHar: { path: "x.har" } } } },
    { use: { ...safe.use, contextOptions: { recordVideo: { dir: "v" } } } },
    { use: { ...safe.use, contextOptions: { storageState: "s.json" } } },
    { effective: { ...effective, trace: "on" } },
    { effective: { ...effective, screenshot: { mode: "only-on-failure" } } },
    { effective: { ...effective, video: "retain-on-failure" } },
    { effective: { ...effective, connectOptions: { wsEndpoint: "ws://127.0.0.1:9" } } },
    { effective: { ...effective, launchOptions: { args: ["--log-net-log=/tmp/n.json"] } } },
    { effective: { trace: "off" } },
    { env: { ...safe.env, DEBUG: "pw:api" } },
    { env: { ...safe.env, DEBUG: "other pw:protocol" } },
    { env: { ...safe.env, DEBUG: "-pw:api,pw:*" } },
    { env: { ...safe.env, DEBUG: "*" } },
    { env: { ...safe.env, NODE_DEBUG: "HTTP" } },
    { env: { ...safe.env, NODE_DEBUG: "ht*" } },
    { env: { ...safe.env, MOZ_LOG: "nsHttp:5" } },
    { env: { ...safe.env, PWDEBUG: "1" } },
    { env: {} },
    { reporters: ["line", J07_REPORTER, "html"] },
    { reporters: ["line", J07_REPORTER, "list"] },
    { reporters: ["line", J07_REPORTER, path.join(path.sep, "tmp", "unreviewed-reporter.cjs")] },
    { reporters: ["line", J07_REPORTER, path.join(__dirname, "studio-embed.reporter.cjs.bak")] },
    { reporters: ["line"] },
    { workers: 2 },
    { retries: 1 },
  ];
  const contextSafe = { combinedContextOptions: { serviceWorkers: "block", colorScheme: "light" }, contextOptions: {}, storageState: undefined };
  const contextVariants = [
    { combinedContextOptions: { recordHar: { path: "x.har" } } },
    { combinedContextOptions: { recordVideo: { dir: "v" } } },
    { combinedContextOptions: { storageState: "s.json" } },
    { contextOptions: { recordHar: { path: "x.har" } } },
    { storageState: "s.json" },
    { combinedContextOptions: undefined },
  ];
  const safeClean = capturePolicyViolations(safe).length === 0 && effectiveContextViolations(contextSafe).length === 0
    && debugLoggingViolations({ DEBUG: "express,-pw:*", NODE_DEBUG: "" }).length === 0;
  const refused = variants.filter((variant) => capturePolicyViolations({ ...safe, ...variant }).length > 0).length
    + contextVariants.filter((variant) => effectiveContextViolations({ ...contextSafe, ...variant }).length > 0).length;
  const total = variants.length + contextVariants.length;
  return { safeClean, variants: total, refused, pass: safeClean && refused === total };
}

/**
 * Worker admission over the RESOLVED runtime, before any login, child start or
 * token: engine qualification (WebKit, G5a engines), the reporter allowlist, debug
 * logging, the configured capture policy and the EFFECTIVE worker-scoped capture
 * values. Returns the first refusal code, or null.
 *   runtime: { browserName, use, workers, retries, reporters, env, effective }
 */
function admitRuntime(resolvedBinding, runtime) {
  if (runtime.browserName === "webkit" && !resolvedBinding.webkitQualified) return "WEBKIT_UNQUALIFIED";
  if (!resolvedBinding.g5a?.qualified) return "G5A_UNQUALIFIED";
  if (!resolvedBinding.g5a.engines.includes(runtime.browserName)) return "G5A_ENGINE_UNQUALIFIED";
  const reporters = reporterViolations(runtime.reporters ?? [], resolvedBinding.accepted ?? []);
  if (reporters.length > 0) return reporters[0];
  const debug = debugLoggingViolations(runtime.env ?? {});
  if (debug.length > 0) return `CAPTURE_${debug[0]}`;
  const effective = effectiveWorkerViolations(runtime.effective);
  if (effective.length > 0) return `CAPTURE_${effective[0]}`;
  const policy = capturePolicyViolations({ ...runtime, accepted: resolvedBinding.accepted ?? [] });
  if (policy.length > 0) return "CAPTURE_POLICY";
  return null;
}

/**
 * Test admission, in the test-scoped fixture before the case object exists (so before
 * any login, child start or token): the effective context options every context of
 * this test will be created with. Returns the first refusal code, or null.
 */
function admitContext(effective) {
  const violations = effectiveContextViolations(effective);
  return violations.length > 0 ? `CAPTURE_${violations[0]}` : null;
}

// ---------------------------------------------------------------- A32/A33 inertness

const TELEMETRY_KEYS = Object.freeze([
  "VITE_GTM_ID", "VITE_GA_ID", "VITE_GOOGLE_ADS_ID", "VITE_GOOGLE_ADS_SIGNUP_LABEL", "VITE_GOOGLE_ADS_ENABLED",
  "VITE_REDDIT_PIXEL_ID", "VITE_REDDIT_ADS_ENABLED", "VITE_TWITTER_PIXEL_ID", "VITE_TWITTER_ADS_ENABLED",
  "VITE_POSTHOG_KEY", "VITE_MIXPANEL_TOKEN", "VITE_MIXPANEL_SESSION_REPLAY_PERCENT", "VITE_SENTRY_DSN",
  "VITE_SENTRY_TRACES_SAMPLE_RATE", "VITE_NEWRELIC_LICENSE_KEY", "VITE_NEWRELIC_ACCOUNT_ID", "VITE_NEWRELIC_TRUST_KEY",
  "VITE_NEWRELIC_DEV_APP_ID", "VITE_NEWRELIC_DEV_AGENT_ID", "VITE_NEWRELIC_PROD_APP_ID", "VITE_NEWRELIC_PROD_AGENT_ID",
  "VITE_GOOGLE_SITE_KEY",
]);
const REQUIRED_TELEMETRY_KEYS = Object.freeze(["VITE_GTM_ID", "VITE_POSTHOG_KEY", "VITE_MIXPANEL_TOKEN", "VITE_SENTRY_DSN", "VITE_NEWRELIC_LICENSE_KEY", "VITE_GOOGLE_SITE_KEY"]);
const INERT = new Set(["", "false", "0", "null", "undefined"]);
const LOOPBACK_URL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** A32/A33 over a descriptor or runtime config: telemetry keys inert, every URL loopback. */
function inertness(value, requireKeys) {
  const seen = new Set();
  let telemetryLive = 0;
  let nonlocalUrls = 0;
  const visit = (node, key) => {
    if (node === null || node === undefined) {
      if (key && TELEMETRY_KEYS.includes(key)) seen.add(key);
      return;
    }
    if (typeof node === "object") {
      for (const [k, v] of Object.entries(node)) visit(v, k);
      return;
    }
    const text = String(node).trim();
    if (key && TELEMETRY_KEYS.includes(key)) {
      seen.add(key);
      if (!INERT.has(text.toLowerCase())) telemetryLive += 1;
    }
    if (/^(https?|wss?):\/\//i.test(text)) {
      let local = false;
      try {
        local = LOOPBACK_URL_HOSTS.has(new URL(text.replace(/^ws/i, "http")).hostname);
      } catch {
        local = false;
      }
      if (!local) nonlocalUrls += 1;
    }
  };
  visit(value, null);
  return {
    telemetryLive,
    nonlocalUrls,
    missingRequired: requireKeys ? REQUIRED_TELEMETRY_KEYS.filter((key) => !seen.has(key)).length : 0,
  };
}

module.exports = {
  ANCESTRY_METHOD,
  BindingRefusal,
  CANONICAL_FIXTURE,
  DEBUG_SENSITIVE,
  G5A_PATHS,
  G5A_REQUIRED_FILES,
  LOG_ROLES,
  NODE_DEBUG_SENSITIVE,
  REQUIRED_OSS_ANCESTOR,
  REQUIRED_OSS_ANCESTORS,
  REQUIRED_OSS_KEYLESS_LISTING,
  WEBKIT_SUCCESSOR,
  admitContext,
  admitRuntime,
  capturePolicySelfTest,
  capturePolicyViolations,
  debugEnabled,
  debugLoggingViolations,
  effectiveContextViolations,
  effectiveWorkerViolations,
  inertness,
  nodeDebugEnabled,
  installedProducer,
  isLoopbackOrigin,
  loadBinding,
  loadSeed,
  protocolParity,
  reporterViolations,
};

// ---------------------------------------------------------------- CLI
// One value-free JSON line on stdout; exit 0 only on an admitted verdict.
function main(argv) {
  const [command, ...rest] = argv;
  const at = rest.indexOf("--binding");
  const file = at >= 0 ? rest[at + 1] : undefined;
  const faRoot = path.resolve(__dirname, "..", "..", "..");
  const emit = (record, code) => {
    process.stdout.write(`${JSON.stringify({ schema: "j07-binding-verdict/v1", command, ...record })}\n`);
    process.exitCode = code;
  };
  try {
    if (command === "verify") {
      const { resolved } = loadBinding(file);
      emit({ verdict: "OK", webkitQualified: resolved.webkitQualified, g5aQualified: resolved.g5a.qualified,
        ossAncestorsProven: resolved.ancestry.proven, appSideSourceRef: resolved.appSide.sourceRefMatches,
        logRoles: Object.keys(resolved.logs).length, debugLogging: debugLoggingViolations(process.env).length }, 0);
    } else if (command === "protocol-parity") {
      const { binding } = loadBinding(file);
      const result = protocolParity(binding, faRoot);
      emit({ verdict: result.pass ? "OK" : "FAIL", ...result }, result.pass ? 0 : 3);
    } else if (command === "policy-selftest") {
      const result = capturePolicySelfTest();
      emit({ verdict: result.pass ? "OK" : "FAIL", ...result }, result.pass ? 0 : 3);
    } else if (command === "scratch-control") {
      // C02-C06 product-mutation arms are Stage A E2 scratch deltas bound one per
      // accepted 713 ledger row; no control id is executable from this module.
      emit({ verdict: "BLOCKED", code: "SCRATCH_CONTROL_UNBOUND" }, 4);
    } else {
      emit({ verdict: "BLOCKED", code: "USAGE" }, 64);
    }
  } catch (error) {
    const code = error instanceof BindingRefusal ? error.code : "INTERNAL";
    emit({ verdict: "BLOCKED", code }, 2);
  }
}

if (require.main === module) main(process.argv.slice(2));
