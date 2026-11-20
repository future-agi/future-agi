import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

// Run the emitted fetch helpers without a browser or a network request.
const api = readFileSync(
  new URL("../src/generated/api-contracts/api.ts", import.meta.url),
  "utf8",
);
function declaration(name) {
  const start = api.indexOf(`export const ${name} =`);
  assert.notEqual(start, -1);
  return api.slice(start, api.indexOf("\nexport ", start + 1));
}
async function helper(name, urlHelper) {
  const source = stripTypeScriptTypes(
    [
      "const apiMutator = (url, options) => options;",
      declaration(urlHelper),
      declaration(name),
    ].join("\n"),
  );
  return (
    await import(
      `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
    )
  )[name];
}
test("upload passes the original Blob to the transport", async () => {
  const upload = await helper(
    "streamAttachmentContent",
    "getStreamAttachmentContentUrl",
  );
  const bytes = new Blob([new Uint8Array([0, 255, 97])]);
  const request = await upload("project", "upload", bytes);
  assert.equal(request.body, bytes);
  assert.equal(
    new Headers(request.headers).get("Content-Type"),
    "application/octet-stream",
  );
});
test("bootstrap forwards the caller's replay key and existing headers", async () => {
  const bootstrap = await helper(
    "bootstrapAttachmentSession",
    "getBootstrapAttachmentSessionUrl",
  );
  const body = { schema_versions: [1] };
  for (let i = 0; i < 2; i++) {
    const request = await bootstrap(body, "same-attempt", {
      headers: new Headers({ "X-Correlation": "test" }),
    });
    assert.equal(
      new Headers(request.headers).get("Idempotency-Key"),
      "same-attempt",
    );
    assert.equal(new Headers(request.headers).get("X-Correlation"), "test");
    assert.deepEqual(JSON.parse(request.body), body);
  }
  await assert.rejects(() => bootstrap(body), /Idempotency-Key/);
});
