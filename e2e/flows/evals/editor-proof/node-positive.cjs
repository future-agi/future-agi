"use strict";
const assert = require("node:assert/strict");
const http = require("node:http");
async function main() {
  assert.ok(globalThis.__editorGuard);
  assert.equal(globalThis.__editorPacketFree, undefined);
  const server = http.createServer((request, response) =>
    response.end("positive-loopback"),
  );
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}`);
    assert.equal(await response.text(), "positive-loopback");
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
  console.log(
    JSON.stringify({
      status: "PASS",
      runtime: "Node",
      loopbackRoundTrip: true,
    }),
  );
}
main().catch(() => {
  console.error("EDITOR_LOOPBACK_CONTROL_FAILED");
  process.exitCode = 1;
});
