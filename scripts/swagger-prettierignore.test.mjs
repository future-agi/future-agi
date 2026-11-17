import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = fileURLToPath(new URL("../", import.meta.url));
const ignore = path.join(root, ".prettierignore");
const prettier = path.join(
  root,
  "frontend/node_modules/prettier/bin/prettier.cjs",
);
const swagger = "api_contracts/openapi/swagger.json";
const ordinary = "api_contracts/openapi/ordinary.json";
const generated = '{\n    "swagger": "2.0",\n    "paths": {}\n}\n';
const ordinaryBytes = '{"ordinary":true}\n';

function run(cwd, command, args) {
  const result = spawnSync(command, args, { cwd, encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  return result.stdout;
}

function fixture(t) {
  const dir = mkdtempSync(path.join(tmpdir(), "swagger-prettierignore-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  mkdirSync(path.join(dir, "api_contracts/openapi"), { recursive: true });
  writeFileSync(path.join(dir, ".prettierignore"), readFileSync(ignore));
  writeFileSync(path.join(dir, swagger), generated);
  writeFileSync(path.join(dir, ordinary), ordinaryBytes);
  run(dir, "git", ["init", "--quiet"]);
  run(dir, "git", ["add", "--", swagger, ordinary]);
  return dir;
}

function format(dir) {
  run(dir, process.execPath, [
    prettier,
    "--no-config",
    "--no-editorconfig",
    "--ignore-path",
    ".prettierignore",
    "--write",
    swagger,
    ordinary,
  ]);
}

test("root ignore lists exactly the generated Swagger path once", () => {
  const matches = readFileSync(ignore, "utf8")
    .split(/\r?\n/)
    .filter((line) => line === swagger);
  assert.equal(matches.length, 1);
});

test("Prettier preserves staged generated Swagger bytes after restaging", (t) => {
  const dir = fixture(t);
  const before = run(dir, "git", ["show", `:${swagger}`]);
  format(dir);
  run(dir, "git", ["add", "--", swagger, ordinary]);
  assert.equal(readFileSync(path.join(dir, swagger), "utf8"), generated);
  assert.equal(run(dir, "git", ["show", `:${swagger}`]), before);
});

test("the Swagger exclusion still lets Prettier format neighboring JSON", (t) => {
  const dir = fixture(t);
  format(dir);
  assert.equal(
    readFileSync(path.join(dir, ordinary), "utf8"),
    '{ "ordinary": true }\n',
  );
  run(dir, "git", ["add", "--", ordinary]);
  assert.equal(
    run(dir, "git", ["show", `:${ordinary}`]),
    '{ "ordinary": true }\n',
  );
});
