import assert from "node:assert/strict";
import { test } from "node:test";
import { disclosureGaps, observedFacts } from "../src/observed.mjs";
import { scanEntry, scanFile } from "../src/scan.mjs";
import { fixture } from "./helpers.mjs";

const manifestOf = (files) => JSON.parse(files.get("omega.hub.json").toString("utf8"));
const blocks = (result) => result.findings.filter((item) => item.severity === "block").map((item) => item.rule).sort();
const flags = (result) => [...new Set(result.findings.filter((item) => item.severity === "flag").map((item) => item.rule))].sort();

test("eval, new Function and a non-literal import() are blocked", () => {
  const files = fixture("eval");
  assert.deepEqual(blocks(scanEntry(files, manifestOf(files))), ["dynamic-import", "eval", "new-function"]);
});

test("createRequire, process.binding, vm, encoded blobs and credential folders are blocked", () => {
  const blob = "A".repeat(2100);
  const text = [
    'import { createRequire } from "node:module";', 'import vm from "node:vm";', "const req = createRequire(import.meta.url);",
    "process.binding('fs');", `const payload = "${blob}";`, 'import os from "node:os"; import path from "node:path";',
    'const creds = path.join(os.homedir(), ".aws", "credentials");', 'const keyfile = "~/.ssh/id_rsa";',
  ].join("\n");
  // the 2,100-character literal also makes its line longer than 2,000 characters
  assert.deepEqual(blocks(scanFile("x.mjs", text)), ["create-require", "encoded-blob", "home-secrets", "home-secrets", "minified", "native-code", "vm"]);
});

test("a 2,001-character line is blocked as minified", () => {
  const files = fixture("minified");
  const result = scanEntry(files, manifestOf(files));
  assert.deepEqual(blocks(result), ["minified"]);
});

const inFile = (result, file) => ({ findings: result.findings.filter((item) => item.file === file) });

test("child_process, env enumeration and self-updating writes are flagged", () => {
  assert.deepEqual(flags(inFile(scanEntry(fixture("child-process"), {}), "helper.mjs")), ["child-process"]);
  const dump = inFile(scanEntry(fixture("env-dump"), {}), "dump.mjs");
  assert.deepEqual(flags(dump), ["env-enumeration"]);
  assert.equal(dump.findings.length, 3); // JSON.stringify, Object.keys and a spread
  const self = inFile(scanEntry(fixture("self-update"), {}), "update.mjs");
  assert.deepEqual(flags(self), ["writes-code"]);
  assert.equal(self.findings.length, 2); // its own module, and main.mjs
});

test("/proc paths and process signalling are flagged: what an entry would turn on its judge", () => {
  const forge = inFile(scanEntry(fixture("forge-verdict"), {}), "main.test.mjs");
  assert.deepEqual(flags(forge), ["proc-access", "signals-processes", "writes-code"]); // its writes go to computed paths
  const text = ['const pid = 7;', 'readFileSync("/proc/1/environ");', "writeFileSync(`/proc/${pid}/fd/1`, \"x\");",
    'process.kill(pid, "SIGKILL");', "process._debugProcess(pid);", 'const fine = "/process/notes"; child.kill();'].join("\n");
  const rules = scanFile("x.mjs", text).findings.map((item) => `${item.rule}:${item.line}`);
  assert.deepEqual(rules, ["proc-access:2", "writes-code:3", "proc-access:3", "signals-processes:4", "signals-processes:5"]);
});

test("curl-probe gets the network flags, and observed lists shell, workers and raw network", () => {
  const files = fixture("curl-probe");
  const manifest = manifestOf(files);
  const scan = scanEntry(files, manifest);
  assert.deepEqual(flags(scan), ["child-process", "fetch-dynamic", "raw-network", "worker-threads"]);
  const observed = observedFacts(scan.facts, manifest);
  assert.ok(observed.shell.length > 0 && observed.workers.length > 0 && observed.rawNetwork.length > 0, JSON.stringify(observed));
  const gaps = disclosureGaps(manifest.disclosure, observed);
  assert.deepEqual(gaps.map((item) => item.message.split(" ")[0]), ["disclosure.shell"]);
  assert.ok(gaps.every((item) => item.rule === "disclosure-gap" && item.severity === "flag"));
});

test("good has no blocks", () => {
  const files = fixture("good");
  assert.deepEqual(blocks(scanEntry(files, manifestOf(files))), []);
});

test("finding ids are stable", () => {
  const files = fixture("eval");
  const one = scanEntry(files, manifestOf(files)).findings.map((item) => item.id);
  const two = scanEntry(files, manifestOf(files)).findings.map((item) => item.id);
  assert.deepEqual(one, two);
  assert.ok(one.every((id) => /^scan-[0-9a-f]{8}$/.test(id)));
});
