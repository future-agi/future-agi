#!/usr/bin/env node
// A second-language runner over the conformance files (DESIGN.md section 22.2): Node's standard library only.
//
//   node futureagi/webhooks/core/conformance/tools/check_node.mjs
//
// It re-derives, in JavaScript, what a TypeScript port of webhooks/core must reproduce: every signature and verify
// result (with its reason id), the jitter of every retry, the sha256 manifest, that each body is exactly
// JSON.stringify(JSON.parse(body)) -- the canonical serialization the vectors pin -- and, from 1.1.0, the B8 intake
// steps a Node route runs before its schema validator, the context size rule, the X-Omega-Deployment-Name rule,
// scrub-before-cut for commit messages and the URL character rule.
import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const CONF = join(dirname(fileURLToPath(import.meta.url)), "..");
const load = (name) => JSON.parse(readFileSync(join(CONF, name), "utf8"));
const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");
const failures = [];
let checks = 0;
const expect = (ok, what) => {
  checks += 1;
  if (!ok) failures.push(what);
};

function sign(secret, msgId, t, body) {
  const key = Buffer.from(secret.slice("whsec_".length), "base64");
  const mac = createHmac("sha256", key);
  mac.update(Buffer.from(`${msgId}.${t}.`, "ascii"));
  mac.update(body);
  return mac.digest("base64");
}

function verify(secret, headers, body, now, tolerance = 300) {
  const h = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  if (!["webhook-id", "webhook-timestamp", "webhook-signature"].every((n) => n in h)) return "missing_headers";
  if (!/^[0-9]{1,12}$/.test(h["webhook-timestamp"])) return "invalid_timestamp";
  const t = Number(h["webhook-timestamp"]);
  if (now - t > tolerance) return "timestamp_too_old";
  if (t - now > tolerance) return "timestamp_too_new";
  const expected = Buffer.from(sign(secret, h["webhook-id"], t, body));
  for (const item of h["webhook-signature"].split(" ")) {
    const comma = item.indexOf(",");
    if (comma < 0 || item.slice(0, comma) !== "v1") continue;
    const value = Buffer.from(item.slice(comma + 1));
    if (value.length === expected.length && timingSafeEqual(value, expected)) return "ok";
  }
  return "no_matching_signature";
}

const canonicalRoundTrip = (buf) => Buffer.from(JSON.stringify(JSON.parse(buf.toString("utf8"))), "utf8").equals(buf);

// signing.json and signing-v1.json
const signing = load("signing.json");
for (const c of signing.sign) {
  const body = Buffer.from(c.body_bytes_b64, "base64");
  const sigs = c.secrets.map((s) => sign(s, c.msg_id, c.timestamp, body));
  expect(JSON.stringify(sigs) === JSON.stringify(c.expected.signatures), `sign ${c.id}`);
  expect(c.expected.headers["webhook-signature"] === sigs.map((s) => `v1,${s}`).join(" "), `sign header ${c.id}`);
}
for (const c of signing.verify) {
  const got = verify(c.secret, c.headers, Buffer.from(c.body_bytes_b64, "base64"), c.now);
  expect(got === c.expected, `verify ${c.id}: ${got} != ${c.expected}`);
}
const v1 = load("signing-v1.json");
const v1body = Buffer.from(v1.body_bytes_b64, "base64");
expect(sign(v1.secret_a, v1.msg_id, v1.timestamp, v1body) === v1.signature_a, "signing-v1 A");
expect(sign(v1.secret_b, v1.msg_id, v1.timestamp, v1body) === v1.signature_b, "signing-v1 B");
expect(sha256(v1body) === v1.sha256 && v1body.length === v1.bytes, "signing-v1 body");

// schedule.json: the jitter of every retry
for (const c of load("schedule.json").next_action) {
  const e = c.expected;
  if (e.action !== "retry") continue;
  const u32 = createHash("sha256").update(`${c.delivery.jitter_seed}:${e.next_attempt}`).digest().readUInt32BE(0);
  const permille = 800 + Math.floor((u32 * 401) / 2 ** 32);
  expect(permille === e.jitter_permille, `jitter ${c.id}`);
  expect(e.delay_ms >= e.base_delay_s * permille, `delay ${c.id}`);
  expect(e.retry_at_ms === c.attempts.at(-1).settled_at_ms + e.delay_ms, `retry_at ${c.id}`);
}

// payloads.json, the B8 fixtures and the delivered event: canonical bytes
for (const c of load("payloads.json").build) {
  const body = Buffer.from(c.expected.body_bytes_b64, "base64");
  expect(sha256(body) === c.expected.sha256 && body.length === c.expected.bytes, `payload digest ${c.id}`);
  expect(body.toString("utf8") === c.expected.body_text, `payload text ${c.id}`);
  expect(canonicalRoundTrip(body), `payload canonical ${c.id}`);
}
for (const f of load("b8/fixtures.json").fixtures) {
  const raw = readFileSync(join(CONF, f.file));
  // Valid fixtures are what the container's JSON.stringify writes; invalid ones may hold bytes no serializer writes.
  expect(sha256(raw) === f.sha256 && raw.length === f.bytes, `fixture digest ${f.file}`);
  if (f.kind === "valid") expect(canonicalRoundTrip(raw), `fixture canonical ${f.file}`);
}
// b8/fixtures.json: the intake steps that need no JSON Schema validator (parse, protocol, identity, context), as EE's
// Node route must run them, and the 1,024-byte context rule measured as EE would (UTF-8 bytes of JSON.stringify).
const bad = (field) => ({ status: 400, code: "invalid_field", field });
function intakePrefix(raw) {
  let doc;
  try {
    doc = JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw));
  } catch {
    return ["parse", bad("")];
  }
  if (doc === null || typeof doc !== "object" || Array.isArray(doc)) return ["parse", bad("")];
  const major = typeof doc.protocol === "string" ? /^omega-deploy-run-event\/v([1-9][0-9]*)$/.exec(doc.protocol) : null;
  const unknownEvent = typeof doc.event === "string" && !["run.completed", "run.failed"].includes(doc.event);
  if ((major && major[1] !== "1") || unknownEvent) return ["protocol", { status: 503, code: "protocol_unsupported" }];
  for (const key of ["deployment", "organization_id", "workspace_id", "project_id", "relay"]) {
    if (Object.hasOwn(doc, key)) return ["identity", { status: 400, code: "identity_not_allowed", field: key }];
  }
  const run = doc.run;
  if (run !== null && typeof run === "object" && !Array.isArray(run)) {
    for (const key of ["deployment_id", "organization_id", "workspace_id", "project_id"]) {
      if (Object.hasOwn(run, key)) return ["identity", { status: 400, code: "identity_not_allowed", field: `run.${key}` }];
    }
    const ctx = run.webhook_context;
    if (ctx !== null && typeof ctx === "object" && !Array.isArray(ctx)) {
      for (const [k, v] of Object.entries(ctx)) {
        const broken = !k.isWellFormed() || (typeof v === "string" && !v.isWellFormed());
        if (broken || (typeof v === "number" && !Number.isFinite(v))) return ["context", bad("run.webhook_context")];
      }
    }
  }
  return null; // the schema and semantic steps need a JSON Schema validator
}
const fixtures = load("b8/fixtures.json");
for (const f of fixtures.fixtures) {
  const raw = readFileSync(join(CONF, f.file));
  const got = intakePrefix(raw);
  if (["parse", "protocol", "identity", "context"].includes(f.step)) {
    expect(got !== null && got[0] === f.step && JSON.stringify(got[1]) === JSON.stringify(f.expect), `intake ${f.file}`);
  } else {
    expect(got === null, `intake ${f.file} must reach the ${f.step} step`);
    const ctx = JSON.parse(raw.toString("utf8")).run?.webhook_context;
    const size = ctx === null || ctx === undefined ? 0 : Buffer.byteLength(JSON.stringify(ctx), "utf8");
    const tooBig = f.step === "semantic" && f.expect.field === "run.webhook_context";
    expect(tooBig ? size > 1024 : size <= 1024, `context size ${f.file}: ${size}`);
  }
}

// payloads.json: the X-Omega-Deployment-Name rule, and commit messages scrubbed, then cut by code points
function pctDecode(s) {
  if (!/^[\x20-\x7e]*$/.test(s)) return null;
  const bytes = [];
  for (let i = 0; i < s.length; ) {
    if (s[i] === "%" && /^[0-9A-Fa-f]{2}$/.test(s.slice(i + 1, i + 3))) {
      bytes.push(parseInt(s.slice(i + 1, i + 3), 16));
      i += 3;
    } else {
      bytes.push(s.charCodeAt(i));
      i += 1;
    }
  }
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(Uint8Array.from(bytes));
  } catch {
    return null;
  }
}
const payloads = load("payloads.json");
for (const c of payloads.deployment_name_header) {
  const name = pctDecode(c.value);
  const want = name === null ? { status: 400, code: "invalid_header", field: "X-Omega-Deployment-Name" } : { name };
  expect(JSON.stringify(want) === JSON.stringify(c.expected), `deployment name header ${c.id}`);
}
const SECRET_SHAPE = /sk-[A-Za-z0-9_-]{8,}|whsec_[A-Za-z0-9+/=]{8,}/g;
for (const c of payloads.build) {
  if (!c.builder.startsWith("prompt.")) continue;
  const key = c.builder === "prompt.version.created" ? "version" : "to_version";
  const src = c.input[key];
  if (!src || src.commit_message == null) continue;
  const body = JSON.parse(Buffer.from(c.expected.body_bytes_b64, "base64").toString("utf8"));
  const scrubbed = Array.from(src.commit_message.replace(SECRET_SHAPE, "[redacted]"));
  const limit = c.variant.payload_mode === "thin" ? 200 : 1000;
  const out = body.data[key];
  expect(out.commit_message === scrubbed.slice(0, limit).join(""), `commit message ${c.id}`);
  expect((out.commit_message_truncated ?? false) === scrubbed.length > limit, `commit truncated ${c.id}`);
}

// addresses.json: outside the authority, a URL holds only U+0021-U+007E unless it is refused for that (url.characters)
for (const c of load("addresses.json").url) {
  const rest = c.url.replace(/^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^/?#]*/, "").split("#")[0];
  const visible = [...rest].every((ch) => ch >= "!" && ch <= "~");
  const rule = c.expected.rule;
  if (rule === "url.characters") expect(!visible, `url characters ${c.id}`);
  else if (rule !== "url.length" && rule !== "url.syntax") expect(visible, `url characters ${c.id}`);
}

const d = load("b8/delivered/run-completed.json");
const dbody = Buffer.from(d.body_bytes_b64, "base64");
expect(verify(d.secret, d.headers, dbody, d.timestamp) === "ok", "delivered verifies");
expect(canonicalRoundTrip(dbody), "delivered canonical");

// manifest.json
const manifest = load("manifest.json").files;
const walk = (dir) =>
  readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? walk(join(dir, n)) : [join(dir, n)]));
const onDisk = walk(CONF)
  .map((p) => relative(CONF, p).split("\\").join("/"))
  .filter((p) => !p.startsWith("tools/") && !p.includes("__pycache__") && !["manifest.json", "README.md"].includes(p));
expect(JSON.stringify(onDisk.sort()) === JSON.stringify(Object.keys(manifest).sort()), "manifest lists every file");
for (const [rel, entry] of Object.entries(manifest)) {
  const buf = readFileSync(join(CONF, rel));
  expect(sha256(buf) === entry.sha256 && buf.length === entry.bytes, `manifest ${rel}`);
}

if (failures.length) {
  console.error(`FAILED ${failures.length} of ${checks} checks:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log(`conformance ${load("manifest.json").conformance_version}: ${checks} checks passed in Node ${process.version}`);
