"use strict";
const assert = require("node:assert/strict");
const net = require("node:net");
const tls = require("node:tls");
const dns = require("node:dns");
const { spawnSync } = require("node:child_process");
async function main() {
  const state = globalThis.__editorGuard;
  assert.ok(state, "required preload is absent");
  assert.ok(
    globalThis.__editorPacketFree,
    "refusal controls require packet-free sentinels",
  );
  process.env.EVAL_GUARD_CONTROL = "1";
  const before = state.allowedCalls;
  for (const call of [
    () => new net.Socket().connect(443, "203.0.113.17"),
    () => net.connect(443, "203.0.113.17"),
    () => net.createConnection({ host: "203.0.113.17", port: 443 }),
    () => tls.connect({ host: "203.0.113.17", port: 443 }),
    () => dns.lookup("editor-control.invalid", () => {}),
    () => dns.resolve4("editor-control.invalid", () => {}),
    () => dns.lookup("127.999.999.999", () => {}),
    () => dns.resolve4("203.0.113.17", () => {}),
  ])
    assert.throws(call, /EDITOR_NONLOCAL_REFUSED/);
  await assert.rejects(
    dns.promises.resolve6("editor-control.invalid"),
    /EDITOR_NONLOCAL_REFUSED/,
  );
  assert.equal(state.allowedCalls, before);
  await assert.rejects(
    fetch("http://editor-control.invalid:443"),
    (error) => error.cause && error.cause.message === "EDITOR_NONLOCAL_REFUSED",
  );
  assert.equal(state.allowedCalls, before);
  const child = spawnSync(
    process.execPath,
    [
      "-e",
      "require('node:assert/strict').throws(()=>require('node:net').connect(443,'203.0.113.17'),/EDITOR_NONLOCAL_REFUSED/)",
    ],
    { env: process.env, encoding: "utf8", timeout: 10000 },
  );
  assert.equal(child.status, 0);
  assert.equal(globalThis.__editorPacketFree.primitiveReached, 0);
  console.log(
    JSON.stringify({
      status: "PASS",
      runtime: "Node",
      refusalControls: 11,
      childInherited: true,
      refusedBeforeOriginal: true,
      packetFreeSentinels: true,
    }),
  );
}
main().catch(() => {
  console.error(
    JSON.stringify({
      status: "FAIL",
      kind: "EDITOR_NODE_CONTROL_FAILED",
      primitiveReached: globalThis.__editorPacketFree?.primitiveReached ?? null,
    }),
  );
  process.exitCode = 1;
});
