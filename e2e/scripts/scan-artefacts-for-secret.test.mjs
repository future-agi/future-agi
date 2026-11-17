import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { deflateRawSync } from "node:zlib";

const script = fileURLToPath(new URL("./scan-artefacts-for-secret.mjs", import.meta.url));
const SECRET = "ts-live-DUMMY-scan-7f3a9c";

function scan(dir, secret = SECRET) {
  const res = spawnSync(process.execPath, [script, dir], { env: { ...process.env, SCAN_SECRET: secret }, encoding: "utf8" });
  return { code: res.status, out: res.stdout + res.stderr };
}

// A one-entry zip (deflated), as Playwright writes traces and blob reports.
function zip(name, content) {
  const data = deflateRawSync(Buffer.from(content));
  const nameBuf = Buffer.from(name);
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(8, 8);
  local.writeUInt32LE(data.length, 18); local.writeUInt32LE(content.length, 22); local.writeUInt16LE(nameBuf.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6); central.writeUInt16LE(8, 10);
  central.writeUInt32LE(data.length, 20); central.writeUInt32LE(content.length, 24); central.writeUInt16LE(nameBuf.length, 28);
  central.writeUInt32LE(0, 42);
  const cdOffset = local.length + nameBuf.length + data.length;
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10);
  end.writeUInt32LE(central.length + nameBuf.length, 12); end.writeUInt32LE(cdOffset, 16);
  return Buffer.concat([local, nameBuf, data, central, nameBuf, end]);
}

function tmpdir(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "scan-secret-"));
  for (const [name, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, name)), { recursive: true });
    fs.writeFileSync(path.join(dir, name), content);
  }
  return dir;
}

test("clean artefacts pass", () => {
  const dir = tmpdir({ "report.json": '{"title":"Fill \\"ts-placeholder-abc\\""}', "trace.zip": zip("trace.trace", "nothing here") });
  const { code, out } = scan(dir);
  assert.equal(code, 0, out);
  assert.match(out, /0 hit\(s\)/);
});

test("finds the raw secret, a Fill step title, and never prints the value", () => {
  const dir = tmpdir({ "results.json": `{"title":"Fill \\"${SECRET}\\""}` });
  const { code, out } = scan(dir);
  assert.equal(code, 1);
  assert.match(out, /HIT .*results\.json \(raw\)/);
  assert.ok(!out.includes(SECRET), "the scanner must not print the secret");
});

test("finds the secret inside a zip (trace, blob report)", () => {
  const dir = tmpdir({ "blob/report.zip": zip("report.jsonl", `{"error":"- Authorization: Bearer ${SECRET}"}`) });
  const { code, out } = scan(dir);
  assert.equal(code, 1);
  assert.match(out, /report\.zip!report\.jsonl \(raw\)/);
});

test("finds the secret in the HTML report's embedded base64 zip", () => {
  const embedded = zip("abc.json", `{"steps":[{"title":"Fill \\"${SECRET}\\""}]}`).toString("base64");
  const dir = tmpdir({ "html/index.html": `<script>window.playwrightReportBase64 = "data:application/zip;base64,${embedded}";</script>` });
  const { code, out } = scan(dir);
  assert.equal(code, 1);
  assert.match(out, /index\.html#embedded-zip!abc\.json \(raw\)/);
});

test("finds base64 and URL-encoded copies at any offset", () => {
  for (const prefix of ["", "x", "xy"]) {
    const dir = tmpdir({ "a.txt": Buffer.from(`${prefix}${SECRET}tail`).toString("base64") });
    const { code, out } = scan(dir);
    assert.equal(code, 1, `prefix ${JSON.stringify(prefix)}: ${out}`);
    assert.match(out, /base64@/);
  }
  const dir = tmpdir({ "u.txt": `key=${encodeURIComponent(`${SECRET}/+=`)}` });
  assert.equal(scan(dir, `${SECRET}/+=`).code, 1);
});

test("refuses to run without a secret", () => {
  const dir = tmpdir({ "a.txt": "x" });
  assert.equal(scan(dir, "").code, 2);
});

test("context output masks every encoding, not just the one that matched", () => {
  const secret = `${SECRET}/`;
  const encoded = encodeURIComponent(secret);
  const b64 = Buffer.from(secret).toString("base64");
  const dir = tmpdir({ "a.txt": `raw ${secret} url ${encoded} b64 ${b64}` });
  const res = spawnSync(process.execPath, [script, dir], { env: { ...process.env, SCAN_SECRET: secret, SCAN_CONTEXT: "1" }, encoding: "utf8" });
  assert.equal(res.status, 1);
  for (const form of [secret, encoded, b64.slice(2, -2)]) {
    assert.ok(!res.stdout.includes(form), `context leaked ${form === secret ? "the secret" : "an encoding"}: ${res.stdout}`);
  }
});

test("base64 copies of a minimum-length (8 character) secret are found at every offset", () => {
  const short = "Ab3$kQ9z";
  for (const prefix of ["", "x", "xy"]) {
    const dir = tmpdir({ "a.txt": Buffer.from(`${prefix}${short}`).toString("base64") });
    const { code, out } = scan(dir, short);
    assert.equal(code, 1, `prefix ${JSON.stringify(prefix)}: ${out}`);
  }
});

test("a missing target is an error, not a clean scan", () => {
  const { code, out } = scan(path.join(os.tmpdir(), "no-such-artefact-dir-1167"));
  assert.equal(code, 2, out);
  assert.match(out, /MISSING/);
});

test("an archive it cannot read is an error, not a clean scan", () => {
  const good = zip("trace.trace", "nothing secret here");
  // A PK header with no end-of-central-directory record.
  const truncated = good.subarray(0, good.length - 22);
  // An entry with a compression method it does not read (12, bzip2).
  const bzip = Buffer.from(good);
  bzip.writeUInt16LE(12, 8);
  bzip.writeUInt16LE(12, 30 + "trace.trace".length + deflateLen(good) + 10);
  // A deflated entry whose data does not inflate.
  const corrupt = Buffer.from(good);
  corrupt.fill(0xff, 30 + "trace.trace".length, 30 + "trace.trace".length + 4);
  for (const [name, bytes] of [["truncated.zip", truncated], ["bzip.zip", bzip], ["corrupt.zip", corrupt]]) {
    const { code, out } = scan(tmpdir({ [name]: bytes }));
    assert.equal(code, 2, `${name}: ${out}`);
    assert.match(out, /UNREADABLE/);
    assert.match(out, /not a clean scan/);
  }
  // Control: the readable archive is still a clean scan.
  assert.equal(scan(tmpdir({ "ok.zip": good })).code, 0);
});

function deflateLen(z) { return z.readUInt32LE(18); }
