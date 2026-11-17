import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { gunzipSync } from "node:zlib";
import { buildCatalog } from "../vendor/omega-hub/src/index.mjs";
import { readTar } from "../src/archive.mjs";
import { catalogDigest, packCanonical } from "../src/pack.mjs";
import { writeTree } from "../src/registry-rules.mjs";
import { fixture } from "./helpers.mjs";

test("packing good twice gives the same bundleSha256, in canonical form", () => {
  const files = fixture("good");
  const first = packCanonical(files);
  const second = packCanonical(new Map([...files].reverse()));
  assert.equal(first.bundleSha256, second.bundleSha256);
  assert.ok(first.bundle.equals(second.bundle));
  const members = readTar(gunzipSync(first.bundle));
  const names = members.map((member) => member.name);
  assert.deepEqual(names, [...names].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b))));
  assert.ok(names.every((name) => name.startsWith("entry/")));
  assert.equal(first.bundle[9], 0xff);
});

test("the digest equals @omega/hub's catalog digest of the same folder", () => {
  const files = fixture("good");
  const manifest = JSON.parse(files.get("omega.hub.json").toString("utf8"));
  const root = mkdtempSync(path.join(tmpdir(), "ohc-digest-"));
  try {
    writeTree(root, { id: "good-agent", source: null, entry: files, sourceFiles: new Map() });
    const [entry] = buildCatalog(root).entries;
    assert.equal(catalogDigest(files, new Map(), manifest), entry.digest);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
