import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

test("root formatting preserves the externally pinned route manifest but formats ordinary JSON", (t) => {
  const root = mkdtempSync(path.join(tmpdir(), "root-format-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const script = path.join(root, "scripts/lint-staged-root-format.mjs");
  const prettier = path.join(root, "frontend/node_modules/.bin/prettier");
  const manifest = path.join(
    root,
    "services/omega-gateway/manifest/console-route-manifest.v1.json",
  );
  const ordinary = path.join(
    root,
    "services/omega-gateway/manifest/route-products.v1.json",
  );
  for (const file of [script, prettier, manifest])
    mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(
    script,
    readFileSync(new URL("./lint-staged-root-format.mjs", import.meta.url)),
  );
  writeFileSync(
    prettier,
    '#!/usr/bin/env node\nrequire("node:fs").writeFileSync(process.env.FORMAT_ARGS, JSON.stringify(process.argv.slice(2)));\n',
    { mode: 0o755 },
  );
  writeFileSync(manifest, '{"pinned":true}\n');
  writeFileSync(ordinary, '{"ordinary":true}\n');
  const capture = path.join(root, "args.json");
  const result = spawnSync(process.execPath, [script, manifest, ordinary], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, FORMAT_ARGS: capture },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(readFileSync(capture, "utf8")), [
    "--write",
    "services/omega-gateway/manifest/route-products.v1.json",
  ]);
  assert.equal(readFileSync(manifest, "utf8"), '{"pinned":true}\n');
});
