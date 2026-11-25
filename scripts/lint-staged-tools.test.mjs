import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

function fixture(t) {
  const dir = mkdtempSync(path.join(tmpdir(), "staged tools "));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const scripts = path.join(dir, "scripts");
  mkdirSync(scripts);
  for (const file of readdirSync(import.meta.dirname).filter((f) =>
    f.endsWith(".mjs"),
  )) {
    copyFileSync(
      path.join(import.meta.dirname, file),
      path.join(scripts, file),
    );
  }
  const frontend = path.join(dir, "frontend");
  const packages = path.join(frontend, "node_modules");
  mkdirSync(path.join(packages, ".bin"), { recursive: true });
  const log = path.join(dir, "calls.ndjson");
  for (const name of ["eslint", "prettier"]) {
    const pkg = path.join(packages, name);
    mkdirSync(pkg);
    const bin = name === "eslint" ? { eslint: "cli.cjs" } : "cli.cjs";
    writeFileSync(path.join(pkg, "package.json"), JSON.stringify({ bin }));
    writeFileSync(
      path.join(pkg, "cli.cjs"),
      `require('node:fs').appendFileSync(${JSON.stringify(log)}, JSON.stringify([${JSON.stringify(name)}, process.cwd(), ...process.argv.slice(2)]) + '\\n'); process.exit(Number(process.env.STAGED_TEST_EXIT) || 0);`,
    );
    // These deliberately non-executable shims must never be the spawn targets.
    writeFileSync(path.join(packages, ".bin", name), "", { mode: 0o644 });
  }
  const run = (script, file, status = 0) => {
    writeFileSync(path.join(dir, file), "");
    return spawnSync(process.execPath, [path.join(scripts, script), file], {
      cwd: dir,
      encoding: "utf8",
      env: { ...process.env, STAGED_TEST_EXIT: String(status) },
    });
  };
  return {
    dir,
    frontend,
    packages,
    run,
    calls: () =>
      readFileSync(log, "utf8")
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line)),
  };
}

const frontendScript = "lint-staged-frontend.mjs";
const rootScript = "lint-staged-root-format.mjs";

test("frontend uses the package entries in order and preserves the filename as one argument", (t) => {
  const f = fixture(t);
  const file = "staged file.ts";
  const result = f.run(frontendScript, path.join("frontend", file));
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(f.calls(), [
    ["eslint", f.frontend, "--fix", file],
    ["prettier", f.frontend, "--write", file],
  ]);
});

test("root formatter uses the repository working directory and returns the tool exit code", (t) => {
  const f = fixture(t);
  const result = f.run(rootScript, "staged file.md", 7);
  assert.equal(result.status, 7, result.stderr);
  assert.deepEqual(f.calls(), [
    ["prettier", f.dir, "--write", "staged file.md"],
  ]);
});

test("eslint failure stops before prettier and keeps its exit code", (t) => {
  const f = fixture(t);
  const result = f.run(frontendScript, path.join("frontend", "sample.js"), 3);
  assert.equal(result.status, 3, result.stderr);
  assert.deepEqual(f.calls(), [["eslint", f.frontend, "--fix", "sample.js"]]);
});

for (const [tool, script, file] of [
  ["eslint", frontendScript, path.join("frontend", "sample.ts")],
  ["prettier", rootScript, "sample.md"],
]) {
  test(`${tool}: malformed package metadata gives the installation hint`, (t) => {
    const f = fixture(t);
    writeFileSync(path.join(f.packages, tool, "package.json"), "{");
    const result = f.run(script, file);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /yarn --cwd frontend install/);
    assert.doesNotMatch(result.stderr, /SyntaxError/);
  });

  test(`${tool}: a directory in place of the declared script gives the installation hint`, (t) => {
    const f = fixture(t);
    const entry = path.join(f.packages, tool, "cli.cjs");
    rmSync(entry);
    mkdirSync(entry);
    const result = f.run(script, file);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /yarn --cwd frontend install/);
    assert.doesNotMatch(result.stderr, /MODULE_NOT_FOUND/);
  });
}
