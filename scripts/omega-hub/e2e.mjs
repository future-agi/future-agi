#!/usr/bin/env node
/**
 * The lane's end-to-end run (SPEC §14.1 acceptance 4), against scripts/omega-hub/dev-up.sh:
 * A uploads first-agent with a Hub token; the real checker takes it to in_review; B approves in the
 * review API; the export lists it as Official; the bundle's SHA-256 matches the header and the
 * metadata; two downloads in a day count once; a report followed by removal turns the bundle into 410.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { writeTarGz } from "../../services/omega-hub-checker/src/archive.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const STATE = process.env.OMEGA_HUB_LANE_STATE ?? path.join(process.env.TMPDIR ?? "/tmp", "omega-hub-lane");
const BASE = process.env.OMEGA_HUB_E2E_URL ?? "http://127.0.0.1:8740";
const API = `${BASE}/omega-hub/v1`;
const FIXTURE = path.join(ROOT, "futureagi", "omega_hub", "tests", "fixtures", "entries", "first-agent");
const seedText = readFileSync(path.join(STATE, "seed.json"), "utf8");
const seed = JSON.parse(seedText.slice(seedText.indexOf("{")));
const tokenA = readFileSync(path.join(STATE, "token-a"), "utf8").trim();
const password = process.env.OMEGA_HUB_DEV_PASSWORD ?? "omega-hub-dev-password";
const step = (text) => console.log(`- ${text}`);
const sha = (data) => createHash("sha256").update(data).digest("hex");

function fail(message) {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

async function call(pathname, { method = "GET", headers = {}, body, expect = 200, raw = false } = {}) {
  const response = await fetch(pathname.startsWith("http") ? pathname : `${API}${pathname}`, { method, headers, body, redirect: "manual" });
  if (response.status !== expect) fail(`${method} ${pathname}: HTTP ${response.status} (wanted ${expect}): ${(await response.text()).slice(0, 400)}`);
  return raw ? response : response.status === 204 ? null : response.json();
}

async function login(email) {
  const response = await fetch(`${BASE}/accounts/token/`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) });
  const body = await response.json().catch(() => ({}));
  const access = body.access ?? body.result?.access ?? body.data?.access;
  if (!response.ok || !access) fail(`login ${email}: HTTP ${response.status} ${JSON.stringify(body).slice(0, 300)}`);
  return { Authorization: `Bearer ${access}` };
}

function folder(dir, prefix = "", out = []) {
  for (const name of readdirSync(dir).sort()) {
    const file = path.join(dir, name);
    if (statSync(file).isDirectory()) folder(file, `${prefix}${name}/`, out);
    else out.push({ name: `entry/${prefix}${name}`, data: readFileSync(file) });
  }
  return out;
}

const token = { "X-Omega-Hub-Token": tokenA };
step(`registry ${API}; maintainers ${seed.maintainerA} (submitter) and ${seed.maintainerB} (reviewer)`);
const form = new FormData();
form.append("publisher", "future-agi");
form.append("name", "first-agent");
form.append("version", "1.0.0");
form.append("channel", "cli");
form.append("bundle", new Blob([writeTarGz(folder(FIXTURE))], { type: "application/gzip" }), "first-agent.tar.gz");
const created = await call("/submissions", { method: "POST", headers: token, body: form, expect: 201 });
step(`A uploaded first-agent with a Hub token: submission ${created.id} is ${created.status}`);

let submission;
for (let i = 0; i < 150; i += 1) {
  submission = await call(`/submissions/${created.id}`, { headers: token });
  if (!["received", "checking"].includes(submission.status)) break;
  await new Promise((resolve) => setTimeout(resolve, 2000));
}
if (submission.status !== "in_review") fail(`the submission is ${submission.status}: ${JSON.stringify(submission.report ?? submission.checkSummary).slice(0, 600)}`);
step(`the real checker took it to in_review: steps ${JSON.stringify(submission.checkSummary.steps)}`);

const b = await login(seed.maintainerB);
const review = await call(`/review/submissions/${created.id}`, { headers: b });
if (review.report.steps.find((s) => s.id === "sandbox-run").network !== "none") fail("the sandbox run did not report network none");
// As a reviewer does (SPEC §5.4): every part the checks observed something for, and the draft left
// as "None.", is rewritten from the observed facts before approving.
const OBSERVED_FOR_PART = { network: ["hosts", "vendors", "rawNetwork"], files: ["filesOutside", "writesCode"], shell: ["shell"],
  background: ["keepsRunning", "workers"], credentials: ["keys", "envEnumeration"] };
const disclosure = { ...review.draftDisclosure };
const rewritten = [];
for (const [part, keys] of Object.entries(OBSERVED_FOR_PART)) {
  const facts = keys.flatMap((key) => review.observed[key] ?? []);
  if (facts.length && /^\s*(none\.?)?\s*$/i.test(disclosure[part] ?? "")) {
    disclosure[part] = `Confirmed in review: ${facts.slice(0, 4).join(", ")}.`.slice(0, 300);
    rewritten.push(part);
  }
}
const decision = {
  decision: "approve", rulesVersion: review.rulesVersion,
  checklist: Object.fromEntries(review.checklist.map((item) => [item.key, true])),
  acknowledgedFlags: review.flags, disclosure,
};
await call(`/review/submissions/${created.id}/decision`, { method: "POST", headers: { ...b, "Content-Type": "application/json" }, body: JSON.stringify(decision) });
step(`B approved it, acknowledging ${review.flags.length} flags and rewriting the disclosure's ${rewritten.join(", ") || "(no)"} part(s) from the observed facts`);

const exported = await call("/export");
const entry = exported.entries.find((item) => item.ref === "future-agi/first-agent");
if (!entry || entry.tier !== "official") fail("the export does not list future-agi/first-agent as official");
step(`the export lists future-agi/first-agent@${entry.latest.version}, tier ${entry.tier}`);

const meta = await call("/entries/future-agi/first-agent/versions/1.0.0");
const headers = { "User-Agent": "omega-e2e/1" };
for (let i = 0; i < 2; i += 1) {
  const response = await call("/entries/future-agi/first-agent/versions/1.0.0/bundle", { headers, raw: true });
  const bytes = Buffer.from(await response.arrayBuffer());
  if (sha(bytes) !== response.headers.get("x-omega-hub-bundle-sha256") || sha(bytes) !== meta.bundleSha256) fail("the bundle SHA-256 does not match");
}
const detail = await call("/entries/future-agi/first-agent");
if (detail.installs30d !== 1) fail(`two downloads in a day counted ${detail.installs30d}`);
step(`the bundle's SHA-256 ${meta.bundleSha256.slice(0, 12)}… matches the header and the metadata; two downloads counted 1`);

const a = await login(seed.maintainerA);
const report = await call("/reports", { method: "POST", headers: { ...a, "Content-Type": "application/json" }, expect: 201,
  body: JSON.stringify({ entry: "future-agi/first-agent", reason: "security", details: "End-to-end run: report, then removal." }) });
await call(`/review/reports/${report.id}/resolve`, { method: "POST", headers: { ...b, "Content-Type": "application/json" },
  body: JSON.stringify({ action: "remove_entry", category: "security issue", note: "End-to-end removal." }) });
await call("/entries/future-agi/first-agent/versions/1.0.0/bundle", { expect: 410, raw: true });
const blocklist = await call("/blocklist");
if (!blocklist.entries.some((item) => item.ref === "future-agi/first-agent")) fail("the blocklist does not list the removed entry");
step("a report followed by removal turns the bundle into 410, and the blocklist lists its digests");
console.log("PASS");
