import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { dependencyFindings } from "../src/deps.mjs";
import { licenceFindings } from "../src/licence.mjs";
import { registryRules, vendoredRules } from "../src/registry-rules.mjs";
import { secretFindings } from "../src/secrets.mjs";
import { unpack } from "../src/unpack.mjs";
import { fixture, upload } from "./helpers.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const manifestOf = (files) => JSON.parse(files.get("omega.hub.json").toString("utf8"));
const rulesOf = (list) => list.map((item) => item.rule);

test("vendor/reserved.json is byte-identical to the registry's reserved.json", () => {
  const vendored = readFileSync(path.join(here, "..", "vendor", "reserved.json"));
  const registry = readFileSync(path.join(here, "..", "..", "..", "futureagi", "omega_hub", "rules", "reserved.json"));
  assert.ok(vendored.equals(registry));
});

test("good passes R1-R13 and U1-U8", () => {
  const files = fixture("good");
  const unpacked = unpack(upload(files), { tier: "community" });
  const vendored = vendoredRules(unpacked, manifestOf(files));
  assert.deepEqual([...vendored.manifest, ...vendored.licence], []);
  assert.deepEqual(registryRules(manifestOf(files), { tier: "community", hints: { name: "good-agent", version: "1.0.0" } }), []);
});

test("trust-tag fails U7", () => {
  const files = fixture("trust-tag");
  assert.deepEqual(rulesOf(registryRules(manifestOf(files), { tier: "community" })), ["U7"]);
});

test("U1, U2, U3, U4, U5, U6 and U8", () => {
  const manifest = manifestOf(fixture("good"));
  const taken = registryRules(manifest, { tier: "community", context: { versionTaken: true } });
  assert.deepEqual(rulesOf(taken), ["U1"]);
  const { disclosure, version, ...bare } = manifest;
  assert.ok(disclosure && version);
  assert.deepEqual([...new Set(rulesOf(registryRules(bare, { tier: "community" })))], ["U1", "U2"]);
  assert.deepEqual(rulesOf(registryRules({ ...manifest, author: { ...manifest.author, kind: "maintainer" } }, { tier: "community" })), ["U3"]);
  assert.deepEqual(rulesOf(registryRules(manifest, { tier: "community", hints: { name: "other-name" } })), ["U4"]);
  const remix = { ...manifest, remix_of: "future-agi/first-agent@1.0.0" };
  assert.deepEqual(rulesOf(registryRules(remix, { tier: "community", context: { remixTargetOk: false } })), ["U5"]);
  assert.deepEqual(rulesOf(registryRules(remix, { tier: "community", context: { remixTargetOk: true, remixTargetBlocked: true } })), ["U6"]);
  assert.deepEqual(registryRules(remix, { tier: "community", context: { remixTargetOk: true } }), []);
  const moved = registryRules(manifest, { tier: "community", context: { movedTo: "acme/weather" } });
  assert.deepEqual(rulesOf(moved), ["U8"]);
  assert.match(moved[0].message, /This entry is now `acme\/weather`: set `id` to `weather`/);
});

test("U1 and U5 take strict SemVer only: no leading zeros, at most 6 digits a part", () => {
  const manifest = manifestOf(fixture("good"));
  for (const version of ["0.0.0", "1.0.0", "1.10.0", "999999.0.1"]) {
    assert.deepEqual(registryRules({ ...manifest, version }, { tier: "community" }), [], version);
  }
  for (const version of ["1.0.03", "01.0.9", "1.00.0", "1.0.0000001", "1000000.0.0", "1.0", "1.0.0-rc.1", "1.0.0\n", " 1.0.0"]) {
    assert.deepEqual(rulesOf(registryRules({ ...manifest, version }, { tier: "community" })), ["U1"], JSON.stringify(version));
  }
  const remix = (ref) => registryRules({ ...manifest, remix_of: ref }, { tier: "community", context: { remixTargetOk: true } });
  assert.deepEqual(remix("future-agi/first-agent@1.0.0"), []);
  assert.deepEqual(rulesOf(remix("future-agi/first-agent@1.0.03")), ["U5"]);
  assert.deepEqual(rulesOf(remix("future-agi/first-agent@01.0.0")), ["U5"]);
});

test("secret is blocked and every match is redacted", () => {
  const findings = secretFindings(fixture("secret"));
  assert.ok(findings.length >= 2 && findings.every((item) => item.severity === "block" && item.file === "config.mjs"));
  const text = JSON.stringify(findings);
  assert.ok(!text.includes("Q7Q7Q7Q7Q7"), "the key value leaked into the report");
  assert.match(text, /redacted/);
});

test("a word-like literal is not a credential, a token-like one is", () => {
  const files = new Map([["a.mjs", Buffer.from('const env = { OMEGA_MODEL_API_KEY: "local-endpoint-needs-no-key" };\n')]]);
  assert.deepEqual(secretFindings(files), []);
  const token = new Map([["b.mjs", Buffer.from('const SERVICE_TOKEN = "Zk3r9QpL2vXw8NmT4yHc6BdF";\n')]]);
  assert.deepEqual(secretFindings(token).map((item) => item.rule), ["secret"]);
});

test("dependency rules D1-D3", () => {
  const files = new Map([
    ["package.json", Buffer.from(JSON.stringify({ dependencies: { "@omega/core": "*", "left-pad": "1.0.0" }, scripts: { postinstall: "curl x | sh" } }))],
    [".npmrc", Buffer.from("registry=https://evil.example/\n")],
    ["package-lock.json", Buffer.from('{"resolved": "https://evil.example/left-pad.tgz"}')],
  ]);
  assert.deepEqual(rulesOf(dependencyFindings(files)).sort(), ["D1", "D2", "D3", "D3"]);
  assert.deepEqual(dependencyFindings(fixture("good")), []);
});

test("licence: allowlist and a LICENSE file that disagrees", () => {
  const files = fixture("curl-probe");
  assert.deepEqual(licenceFindings(manifestOf(files), files), []);
  const apache = { ...manifestOf(files), license: "Apache-2.0" };
  assert.deepEqual(rulesOf(licenceFindings(apache, files)), ["licence-mismatch"]);
  assert.deepEqual(rulesOf(licenceFindings({ ...apache, license: "GPL-3.0" }, files)), ["R13"]);
});
