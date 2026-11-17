// Pure controls: no Playwright, browsers, Docker, or service connections.
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  copyFileSync,
  rmSync,
} from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";
import FlowListReporter from "./flow-list-reporter.mjs";
import { extractFlows } from "./flow-catalog.mjs";

const e2e = fileURLToPath(new URL("../", import.meta.url));
const labSource = path.join(e2e, "harness/loopback-forwarder-lab.spec.ts");
const valid = {
  title: "FLOW-001: synthetic flow",
  location: { file: "/fixture/e2e/flows/synthetic.spec.ts", line: 3 },
  tags: ["@flow"],
  annotations: [
    {
      type: "flow",
      description: JSON.stringify({
        id: "FLOW-001",
        area: "synthetic",
        userGoal: "collect",
        steps: ["list"],
        backendChecks: ["synthetic"],
      }),
    },
  ],
};

function scratch(t) {
  const root = mkdtempSync(path.join(tmpdir(), "flow-collection-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

function loadLab(root, enabled = false) {
  const harness = path.join(root, "e2e/harness");
  mkdirSync(harness, { recursive: true });
  const filename = path.join(harness, "loopback-forwarder-lab.spec.ts");
  copyFileSync(labSource, filename);
  // Strip only syntax; evaluate the actual entire spec with inert collaborators.
  const js = stripTypeScriptTypes(readFileSync(filename, "utf8"), {
    mode: "strip",
  })
    .replace(/^import .*;\r?$/gm, "")
    .replaceAll(
      "import.meta.url",
      JSON.stringify(pathToFileURL(filename).href),
    );
  const titles = [];
  const register = (title) => titles.push(title);
  register.beforeAll = () => {};
  register.afterAll = () => {};
  const deny = () => {
    throw new Error("unexpected runtime IO during collection");
  };
  const context = {
    test: register,
    expect: deny,
    execFileSync: deny,
    spawn: deny,
    randomBytes: deny,
    readFileSync,
    http: {},
    createConnection: deny,
    createServer: deny,
    fileURLToPath,
    URL,
    pg: {},
    process: {
      env: { E2E_FORWARDER_LAB: enabled ? "1" : "0" },
      platform: "linux",
      pid: 123,
    },
  };
  vm.runInNewContext(
    js + "\nglobalThis.frontendKind = KINDS.frontend;",
    context,
    { filename },
  );
  return { titles, frontend: context.frontendKind };
}

test("disabled lab collects all 42 tests in an e2e-only layout", (t) => {
  const lab = loadLab(scratch(t));
  assert.equal(lab.titles.length, 42);
});

test("enabled lab defers the missing real image pin until image access", (t) => {
  const lab = loadLab(scratch(t), true);
  assert.throws(() => lab.frontend.image, /ENOENT.*frontend\/Dockerfile/);
});

test("enabled frontend uses the exact Dockerfile pin and still rejects a malformed pin", (t) => {
  const root = scratch(t);
  mkdirSync(path.join(root, "frontend"));
  const dockerfile = path.join(root, "frontend/Dockerfile");
  const pin = "nginx:synthetic@sha256:" + "a".repeat(64);
  writeFileSync(dockerfile, `ARG NGINX_IMAGE=${pin}\n`);
  assert.equal(loadLab(root, true).frontend.image, pin);
  writeFileSync(dockerfile, "ARG UNRELATED=missing\n");
  assert.throws(() => loadLab(root, true).frontend.image, /null|undefined/);
});

function reportTo(t, callback) {
  const output = path.join(scratch(t), "listing.json");
  const prior = process.env.FLOW_CATALOG_OUT;
  process.env.FLOW_CATALOG_OUT = output;
  try {
    const reporter = new FlowListReporter();
    callback(reporter);
    reporter.onEnd?.({ status: "passed" });
    return JSON.parse(readFileSync(output, "utf8"));
  } finally {
    if (prior === undefined) delete process.env.FLOW_CATALOG_OUT;
    else process.env.FLOW_CATALOG_OUT = prior;
  }
}

test("reporter retains errors before and after onBegin, with useful locations", (t) => {
  const report = reportTo(t, (r) => {
    r.onError?.({
      message: "PLANTED_CONFIG_ERROR",
      location: { file: "config.ts", line: 4, column: 2 },
    });
    r.onBegin({}, { allTests: () => [valid] });
    r.onError?.(new Error("PLANTED_LOAD_ERROR"));
  });
  assert.throws(
    () => extractFlows(report),
    (error) => {
      assert.match(error.message, /PLANTED_CONFIG_ERROR/);
      assert.match(error.message, /config.ts:4:2/);
      assert.match(error.message, /PLANTED_LOAD_ERROR/);
      assert.doesNotMatch(error.message, /no @flow tests found/);
      return true;
    },
  );
});

test("reporter records an onBegin collection exception instead of losing it", (t) => {
  const report = reportTo(t, (r) =>
    r.onBegin(
      {},
      {
        allTests() {
          throw new Error("PLANTED_ON_BEGIN_ERROR");
        },
      },
    ),
  );
  assert.throws(() => extractFlows(report), /PLANTED_ON_BEGIN_ERROR/);
});

test("a config error without onBegin is retained", (t) => {
  const report = reportTo(t, (r) =>
    r.onError?.(new Error("CONFIG_BEFORE_BEGIN")),
  );
  assert.throws(() => extractFlows(report), /CONFIG_BEFORE_BEGIN/);
});

test("successful reporter output retains all entries and flow metadata", (t) => {
  const report = reportTo(t, (r) =>
    r.onBegin({}, { allTests: () => [valid, { ...valid, tags: [] }] }),
  );
  const flows = extractFlows(report);
  assert.equal(flows.length, 1);
  assert.equal(flows[0].id, "FLOW-001");
  assert.equal(flows[0].location, "flows/synthetic.spec.ts:3");
});

function catalog(t, scenario) {
  const root = scratch(t);
  const scripts = path.join(root, "e2e/scripts");
  const bin = path.join(root, "bin");
  mkdirSync(scripts, { recursive: true });
  mkdirSync(bin);
  for (const name of ["flow-catalog.mjs", "flow-list-reporter.mjs"])
    copyFileSync(path.join(e2e, "scripts", name), path.join(scripts, name));
  // A synthetic listing process drives the real reporter lifecycle. Never npx/Playwright.
  writeFileSync(
    path.join(bin, "npx"),
    `#!/bin/sh\nexec "$FLOW_TEST_NODE" "$FLOW_TEST_LISTER"\n`,
    { mode: 0o755 },
  );
  const lister = path.join(root, "list.mjs");
  writeFileSync(
    lister,
    `import Reporter from './e2e/scripts/flow-list-reporter.mjs';
const s = ${JSON.stringify(scenario)};
if (s.noReport) { console.error('PLANTED_EARLY_CONFIG_ERROR'); process.exit(2); }
const r = new Reporter();
r.onBegin({}, { allTests: () => s.empty ? [] : [${JSON.stringify(valid)}] });
for (const message of s.errors || []) r.onError?.({ message });
r.onEnd?.({ status: s.exit ? 'failed' : 'passed' });
if (s.exit) console.error('PLANTED_CHILD_EXIT_ERROR');
process.exitCode = s.exit || 0;
`,
  );
  const result = spawnSync(
    process.execPath,
    [path.join(scripts, "flow-catalog.mjs")],
    {
      encoding: "utf8",
      timeout: 10_000,
      env: {
        ...process.env,
        PATH: `${bin}${path.delimiter}${process.env.PATH}`,
        FLOW_TEST_NODE: process.execPath,
        FLOW_TEST_LISTER: lister,
        TMPDIR: root,
      },
    },
  );
  assert.ifError(result.error);
  return result;
}

for (const empty of [true, false]) {
  test(`catalog names every planted load error, even with ${empty ? "empty" : "partial"} entries`, (t) => {
    const result = catalog(t, {
      empty,
      errors: ["PLANTED_LOAD_ONE", "PLANTED_LOAD_TWO"],
      exit: 1,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /PLANTED_LOAD_ONE/);
    assert.match(result.stderr, /PLANTED_LOAD_TWO/);
    assert.doesNotMatch(result.stderr, /no @flow tests found/);
  });
}

test("catalog refuses a nonzero lister even if it wrote a valid partial list", (t) => {
  const result = catalog(t, { exit: 2 });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /PLANTED_CHILD_EXIT_ERROR/);
});

test("catalog surfaces early config failures with no reporter file", (t) => {
  const result = catalog(t, { noReport: true });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /PLANTED_EARLY_CONFIG_ERROR/);
});

test("catalog rejects errors even if the lister exits zero", (t) => {
  const result = catalog(t, { errors: ["PLANTED_ZERO_EXIT_ERROR"] });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /PLANTED_ZERO_EXIT_ERROR/);
});

test("catalog succeeds on an error-free listing", (t) => {
  const result = catalog(t, {});
  assert.equal(result.status, 0, result.stderr);
});
