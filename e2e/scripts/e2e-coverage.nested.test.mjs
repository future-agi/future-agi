import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  copyFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";

// The app usually sits at the git root, but a checkout can also nest it in a
// subdirectory such as core/. Run the real CLI against a real git diff in both
// layouts, not just classifyChange with already-normalized synthetic paths.
for (const prefix of ["", "core"]) {
  for (const scenario of ["new-page", "accounting-migration"]) {
    test(`coverage CLI blocks ${scenario} in ${prefix || "root"} layout`, (t) => {
      const root = mkdtempSync(path.join(tmpdir(), "gateway-coverage-"));
      t.after(() => rmSync(root, { recursive: true, force: true }));
      const app = path.join(root, prefix);
      const env = {
        ...process.env,
        GIT_AUTHOR_NAME: "Coverage Fixture",
        GIT_AUTHOR_EMAIL: "coverage@example.com",
        GIT_COMMITTER_NAME: "Coverage Fixture",
        GIT_COMMITTER_EMAIL: "coverage@example.com",
      };
      const git = (...args) =>
        execFileSync("git", ["-C", root, ...args], {
          env,
          encoding: "utf8",
        }).trim();
      const write = (relative, text) => {
        const file = path.join(app, relative);
        mkdirSync(path.dirname(file), { recursive: true });
        writeFileSync(file, text);
      };
      git("init", "-q");
      mkdirSync(path.join(app, "e2e/scripts"), { recursive: true });
      copyFileSync(
        new URL("./e2e-coverage.mjs", import.meta.url),
        path.join(app, "e2e/scripts/e2e-coverage.mjs"),
      );
      copyFileSync(
        new URL("./flow-ids.mjs", import.meta.url),
        path.join(app, "e2e/scripts/flow-ids.mjs"),
      );
      git("add", ".");
      git("commit", "-qm", "Synthetic coverage baseline");
      const base = git("rev-parse", "HEAD");
      if (scenario === "new-page") {
        write(
          "frontend/src/pages/gateway/accounting.jsx",
          "export default function Accounting() { return null; }\n",
        );
      } else {
        write(
          "futureagi/agentcc/migrations/0029_request_log_usage_state.py",
          'from django.db import migrations\noperations = [migrations.AddField(name="usage_state")]\n',
        );
        write(
          "futureagi/agentcc/serializers/request_log.py",
          'fields = ["usage_state"]\n',
        );
      }
      git("add", ".");
      git("commit", "-qm", "Synthetic user-visible change");
      // A declaration cannot waive a new surface. This also pins the exact
      // block verdict rather than the separate missing-marker result.
      const body = path.join(root, "pr-body.txt");
      writeFileSync(body, "E2E: exempt (unclassified)\n");
      const result = spawnSync(
        process.execPath,
        [
          path.join(app, "e2e/scripts/e2e-coverage.mjs"),
          "--base",
          base,
          "--title",
          "fix(gateway): preserve native usage",
          "--body",
          body,
          "--json",
        ],
        { env, encoding: "utf8" },
      );
      assert.equal(result.stderr, "");
      const report = JSON.parse(result.stdout);
      assert.equal(report.classification, "NEW-FLOW");
      assert.equal(report.verdict, "block");
      assert.equal(result.status, 1);
      assert.ok(report.files.every((file) => !file.path.startsWith("core/")));
    });
  }
}

// `E2E: new <ID>` is proven from the specs, not a committed catalog: the id
// must be declared under e2e/flows at head and absent at the merge base.
const spec = (id) =>
  `test('${id}: probe', { tag: ['@flow'], annotation: flowAnnotation({ id: '${id}', area: 'observe' }) }, async () => {});\n`;
for (const prefix of ["", "core"]) {
  for (const [declared, verdict] of [
    ["OBS-E2E-002", "pass"],
    ["OBS-E2E-001", "block"],
  ]) {
    test(`coverage CLI gives ${verdict} for new ${declared} in ${prefix || "root"} layout`, (t) => {
      const root = mkdtempSync(path.join(tmpdir(), "flow-id-coverage-"));
      t.after(() => rmSync(root, { recursive: true, force: true }));
      const app = path.join(root, prefix);
      const env = {
        ...process.env,
        GIT_AUTHOR_NAME: "Coverage Fixture",
        GIT_AUTHOR_EMAIL: "coverage@example.com",
        GIT_COMMITTER_NAME: "Coverage Fixture",
        GIT_COMMITTER_EMAIL: "coverage@example.com",
      };
      const git = (...args) =>
        execFileSync("git", ["-C", root, ...args], {
          env,
          encoding: "utf8",
        }).trim();
      const write = (relative, text) => {
        const file = path.join(app, relative);
        mkdirSync(path.dirname(file), { recursive: true });
        writeFileSync(file, text);
      };
      git("init", "-q");
      mkdirSync(path.join(app, "e2e/scripts"), { recursive: true });
      copyFileSync(
        new URL("./e2e-coverage.mjs", import.meta.url),
        path.join(app, "e2e/scripts/e2e-coverage.mjs"),
      );
      copyFileSync(
        new URL("./flow-ids.mjs", import.meta.url),
        path.join(app, "e2e/scripts/flow-ids.mjs"),
      );
      write("e2e/flows/observe/existing.spec.ts", spec("OBS-E2E-001"));
      git("add", ".");
      git("commit", "-qm", "Synthetic coverage baseline");
      const base = git("rev-parse", "HEAD");
      write(
        "frontend/src/pages/observe/probe.jsx",
        "export default function Probe() { return null; }\n",
      );
      write("e2e/flows/observe/probe.spec.ts", spec("OBS-E2E-002"));
      git("add", ".");
      git("commit", "-qm", "Synthetic page with its flow");
      const body = path.join(root, "pr-body.txt");
      writeFileSync(body, `E2E: new ${declared}\n`);
      const result = spawnSync(
        process.execPath,
        [
          path.join(app, "e2e/scripts/e2e-coverage.mjs"),
          "--base",
          base,
          "--title",
          "feat(observe): add a probe page",
          "--body",
          body,
          "--json",
        ],
        { env, encoding: "utf8" },
      );
      assert.equal(result.stderr, "");
      const report = JSON.parse(result.stdout);
      assert.equal(report.classification, "NEW-FLOW");
      assert.equal(report.verdict, verdict, report.explanation);
    });
  }
}

test("coverage CLI blocks `E2E: new` for a flow on disk but not at --head", (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "flow-id-uncommitted-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const env = {
    ...process.env,
    GIT_AUTHOR_NAME: "Coverage Fixture",
    GIT_AUTHOR_EMAIL: "coverage@example.com",
    GIT_COMMITTER_NAME: "Coverage Fixture",
    GIT_COMMITTER_EMAIL: "coverage@example.com",
  };
  const git = (...args) =>
    execFileSync("git", ["-C", root, ...args], {
      env,
      encoding: "utf8",
    }).trim();
  const write = (relative, text) => {
    const file = path.join(root, relative);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, text);
  };
  git("init", "-q");
  mkdirSync(path.join(root, "e2e/scripts"), { recursive: true });
  for (const name of ["e2e-coverage.mjs", "flow-ids.mjs"]) {
    copyFileSync(
      new URL(`./${name}`, import.meta.url),
      path.join(root, "e2e/scripts", name),
    );
  }
  write("e2e/flows/observe/existing.spec.ts", spec("OBS-E2E-001"));
  git("add", ".");
  git("commit", "-qm", "Synthetic coverage baseline");
  const base = git("rev-parse", "HEAD");
  write(
    "frontend/src/pages/observe/probe.jsx",
    "export default function Probe() { return null; }\n",
  );
  git("add", ".");
  git("commit", "-qm", "Synthetic page without its flow");
  // On disk, never committed: the head does not have this flow.
  write("e2e/flows/observe/probe.spec.ts", spec("OBS-E2E-002"));
  const body = path.join(root, "pr-body.txt");
  writeFileSync(body, "E2E: new OBS-E2E-002\n");
  const result = spawnSync(
    process.execPath,
    [
      path.join(root, "e2e/scripts/e2e-coverage.mjs"),
      "--base",
      base,
      "--title",
      "feat(observe): add a probe page",
      "--body",
      body,
      "--json",
    ],
    { env, encoding: "utf8" },
  );
  assert.equal(result.stderr, "");
  const report = JSON.parse(result.stdout);
  assert.equal(report.verdict, "block", report.explanation);
  assert.match(report.explanation, /OBS-E2E-002/);
});
