// The loopback-forwarder probe route the harness mocks share (lib/forward-probe.mjs):
// GET /__probe/<key id>/<nonce> answers HMAC-SHA256(key, nonce), the key being the one
// planted at /tmp/fwdprobe-keys/<key id> inside the mock's container, and answers
// nothing without a well-formed key; GET /__probed/<nonce> counts the arrivals.
// mock-slack serves the same route but listens at import, so the live lab
// (harness/loopback-forwarder-lab.spec.ts) covers it in a real container.
import assert from "node:assert/strict";
import { createHmac, randomBytes } from "node:crypto";
import { EventEmitter } from "node:events";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import test from "node:test";

const KEYS = "/tmp/fwdprobe-keys";
const SERVERS = ["mock-llm", "webhook-sink"];
const handlers = {};
for (const name of SERVERS) {
  const url = new URL(`../stack/${name}/server.mjs`, import.meta.url);
  // Never import a module that listens at evaluation.
  assert.match(readFileSync(url, "utf8"), /export function handleRequest\(/);
  handlers[name] = (await import(url.href)).handleRequest;
}

function call(name, path) {
  const req = Object.assign(new EventEmitter(), {
    method: "GET",
    url: path,
    headers: {},
  });
  const res = {
    status: 0,
    headers: {},
    data: "",
    ended: false,
    writeHead(status, headers) {
      this.status = status;
      this.headers = headers;
    },
    write(data) {
      this.data += data;
    },
    end(data = "") {
      this.data += data;
      this.ended = true;
    },
    destroy() {
      this.ended = true;
    },
  };
  handlers[name](req, res);
  req.emit("end");
  assert.equal(res.ended, true);
  return { status: res.status, body: JSON.parse(res.data) };
}

const fresh = () => `fwdprobe-${randomBytes(16).toString("hex")}`;
/** Plants `content` as a probe key, as the harness does inside a container. */
function plant(content) {
  const keyId = fresh();
  mkdirSync(KEYS, { recursive: true, mode: 0o700 });
  writeFileSync(`${KEYS}/${keyId}`, content, { mode: 0o600 });
  return keyId;
}

for (const name of SERVERS) {
  test(`${name} answers a probe with HMAC-SHA256 of its planted key and counts its arrival`, () => {
    const key = randomBytes(32);
    const keyId = plant(key.toString("hex"));
    try {
      const [first, second] = [fresh(), fresh()];
      const answered = call(name, `/__probe/${keyId}/${first}`);
      assert.deepEqual(answered, {
        status: 200,
        body: { answer: createHmac("sha256", key).update(first).digest("hex") },
      });
      assert.deepEqual(call(name, `/__probe/${keyId}/${second}`), {
        status: 200,
        body: {
          answer: createHmac("sha256", key).update(second).digest("hex"),
        },
      });
      assert.deepEqual(call(name, `/__probed/${first}`), {
        status: 200,
        body: { count: 1 },
      });
      assert.deepEqual(call(name, `/__probed/${fresh()}`), {
        status: 200,
        body: { count: 0 },
      });
    } finally {
      rmSync(`${KEYS}/${keyId}`, { force: true });
    }
  });

  test(`${name} answers no probe without a well-formed planted key, but still counts its arrival`, () => {
    // A key with a trailing newline is not the 64 hex characters the harness writes.
    const malformed = plant(`${randomBytes(32).toString("hex")}\n`);
    try {
      for (const keyId of [fresh(), malformed]) {
        const nonce = fresh();
        const { status, body } = call(name, `/__probe/${keyId}/${nonce}`);
        assert.equal(status, 404);
        assert.equal(body.answer, undefined);
        assert.deepEqual(call(name, `/__probed/${nonce}`).body, { count: 1 });
      }
    } finally {
      rmSync(`${KEYS}/${malformed}`, { force: true });
    }
  });
}
