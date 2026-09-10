import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { generate } from "./gen-media-fixtures.mjs";

const E2E_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const MEDIA_DIR = path.join(E2E_DIR, "fixtures", "media");

// Regenerate before asserting so the selftest is self-contained (doesn't
// depend on a prior manual run having written the files).
generate();

test("pixel.png is a valid 1x1 PNG, 68 bytes", () => {
  const buf = readFileSync(path.join(MEDIA_DIR, "pixel.png"));
  assert.equal(buf.length, 68);
  assert.deepEqual(buf.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
});

test("tone-1s.wav is a valid 1-second PCM16 mono 8000Hz RIFF/WAVE, 16044 bytes", () => {
  const buf = readFileSync(path.join(MEDIA_DIR, "tone-1s.wav"));
  assert.equal(buf.length, 16044);
  assert.equal(buf.toString("ascii", 0, 4), "RIFF");
  assert.equal(buf.toString("ascii", 8, 12), "WAVE");
  assert.equal(buf.toString("ascii", 12, 16), "fmt ");
  assert.equal(buf.toString("ascii", 36, 40), "data");
});

test("sample.pdf is a minimal valid single-page PDF, 437 bytes", () => {
  const buf = readFileSync(path.join(MEDIA_DIR, "sample.pdf"));
  assert.equal(buf.length, 437);
  assert.equal(buf.toString("latin1", 0, 4), "%PDF");
  assert.match(buf.toString("latin1"), /%%EOF\s*$/);
  assert.match(buf.toString("latin1"), /\/Type\s*\/Page\b/);
});

test("variables.csv has a topic header and 2 data rows, 43 bytes", () => {
  const buf = readFileSync(path.join(MEDIA_DIR, "variables.csv"));
  assert.equal(buf.length, 43);
  const lines = buf.toString("utf8").trim().split("\n");
  assert.equal(lines[0], "topic");
  assert.equal(lines.length, 3);
});
