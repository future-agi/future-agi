import assert from "node:assert/strict";
import { test } from "node:test";
import { unpack } from "../src/unpack.mjs";
import { fixture, rawMember, upload, zip } from "./helpers.mjs";

const rules = (result) => result.findings.map((item) => item.rule);

test("good unpacks in every accepted layout and in a zip", () => {
  for (const layout of ["entry", "folder", "root"]) {
    const result = unpack(upload(fixture("good"), { layout }), { tier: "community" });
    assert.deepEqual(result.findings, [], layout);
    assert.equal(result.id, "good-agent");
    assert.ok(result.entry.has("main.mjs"));
  }
  const zipped = unpack(zip(new Map([...fixture("good")].map(([rel, data]) => [`entry/${rel}`, data]))), { tier: "community" });
  assert.deepEqual(zipped.findings, []);
});

test("symlinks, '..' paths and absolute paths are blocked", () => {
  const good = fixture("good");
  const link = unpack(upload(good, { extra: [{ name: "entry/evil", type: "2", link: "/etc/passwd" }] }), { tier: "community" });
  assert.deepEqual(rules(link), ["link"]);
  const dotdot = unpack(upload(good, { extra: [{ name: "entry/../../etc/cron.d/x", data: Buffer.from("x") }] }), { tier: "community" });
  assert.deepEqual(rules(dotdot), ["path"]);
  const absolute = unpack(upload(good, { extra: [{ name: "/tmp/x", data: Buffer.from("x") }] }), { tier: "community" });
  assert.deepEqual(rules(absolute), ["path"]);
});

test("opaque files are blocked by name and by content", () => {
  const binary = unpack(upload(fixture("binary")), { tier: "community" });
  assert.deepEqual(binary.findings.map((item) => item.file).sort(), ["helper.ts", "tool"]);
  assert.ok(binary.findings.every((item) => item.rule === "opaque-file" && item.severity === "block"));
  const archive = unpack(upload(fixture("archive")), { tier: "community" });
  assert.deepEqual(archive.findings.map((item) => [item.rule, item.file]), [["opaque-file", "vendor.zip"]]);
});

test("a source folder is for Official entries only, and limits hold", () => {
  const files = new Map([...fixture("good")].map(([rel, data]) => [`entry/${rel}`, data]));
  files.set("source/SKILL.md", Buffer.from("# skill\n"));
  const members = [...files].map(([name, data]) => ({ name, data }));
  const community = unpack(upload(new Map(), { layout: "root", extra: members }), { tier: "community" });
  assert.deepEqual(rules(community), ["source"]);
  const big = Buffer.alloc(2 * 1024 * 1024 + 1, 1);
  assert.deepEqual(rules(unpack(big, { tier: "community" })), ["size"]);
});

test("macOS metadata is ignored, with a note", () => {
  const result = unpack(upload(fixture("good"), { extra: [{ name: "entry/._main.mjs", data: Buffer.from([0, 5, 22, 7]) }] }), { tier: "community" });
  assert.deepEqual(result.findings, []);
  assert.match(result.notes[0], /macOS metadata/);
});

test("rawMember builds what the reader expects", () => {
  assert.equal(rawMember({ name: "a", data: Buffer.from("x") }).length, 1024);
});
