import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import test from "node:test";

const serverURL = new URL("../stack/webhook-sink/server.mjs", import.meta.url);
// Never import a module that listens at evaluation.
assert.match(
  readFileSync(serverURL, "utf8"),
  /export function handleRequest\(/,
);
const { handleRequest } = await import(serverURL.href);

function call(method, url, body = "", headers = {}) {
  const req = Object.assign(new EventEmitter(), { method, url, headers });
  const res = {
    status: 0,
    headers: {},
    data: "",
    ended: false,
    writeHead(status, h) {
      this.status = status;
      this.headers = h;
    },
    end(data = "") {
      this.data += data;
      this.ended = true;
    },
    destroy() {
      this.ended = true;
    },
  };
  handleRequest(req, res);
  req.emit(
    "data",
    Buffer.from(typeof body === "string" ? body : JSON.stringify(body)),
  );
  req.emit("end");
  assert.equal(res.ended, true);
  return { status: res.status, body: JSON.parse(res.data) };
}
const listed = (channel) => call("GET", `/__received/${channel}`).body;

test("records every request to a channel, oldest first, and answers 200", () => {
  const event = { event: "batch.completed", data: { batch_id: "b-1" } };
  assert.deepEqual(
    call("POST", "/r/ch-rec/hook", event, {
      "x-agentcc-signature": "sha256=ab",
    }),
    { status: 200, body: { ok: true } },
  );
  assert.equal(
    call("POST", "/r/ch-rec/hook?retry=1", "plain text").status,
    200,
  );
  const { channel, requests } = listed("ch-rec");
  assert.equal(channel, "ch-rec");
  assert.deepEqual(
    requests.map(({ method, path, headers, body, status }) => ({
      method,
      path,
      headers,
      body,
      status,
    })),
    [
      {
        method: "POST",
        path: "/r/ch-rec/hook",
        headers: { "x-agentcc-signature": "sha256=ab" },
        body: event,
        status: 200,
      },
      {
        method: "POST",
        path: "/r/ch-rec/hook?retry=1",
        headers: {},
        body: "plain text",
        status: 200,
      },
    ],
  );
  assert.ok(requests.every((r) => !Number.isNaN(Date.parse(r.at))));
});

test("fail/<n> answers 500 to the channel's first n requests, then 200, and records each status", () => {
  const statuses = [1, 2, 3, 4].map(
    () => call("POST", "/r/ch-fail/fail/2/hook", { n: 1 }).status,
  );
  assert.deepEqual(statuses, [500, 500, 200, 200]);
  assert.deepEqual(
    listed("ch-fail").requests.map((r) => r.status),
    [500, 500, 200, 200],
  );
  assert.deepEqual(call("POST", "/r/ch-fail-0/fail/0/hook").body, { ok: true });
});

test("channels are isolated; unknown channels list nothing; other paths are 404 and unrecorded", () => {
  call("POST", "/r/ch-iso-a/hook", { a: 1 });
  call("POST", "/r/ch-iso-b/hook", { b: 1 });
  assert.deepEqual(
    listed("ch-iso-a").requests.map((r) => r.body),
    [{ a: 1 }],
  );
  assert.deepEqual(
    listed("ch-iso-b").requests.map((r) => r.body),
    [{ b: 1 }],
  );
  assert.deepEqual(listed("ch-never"), { channel: "ch-never", requests: [] });
  for (const path of [
    "/hook",
    "/r/",
    "/r/bad channel/hook",
    "/r/ch-iso-a/fail/x/hook",
    "/__received/ch-iso-a/x",
  ]) {
    assert.equal(call("POST", path).status, 404, path);
  }
  assert.equal(listed("ch-iso-a").requests.length, 1);
  assert.deepEqual(call("GET", "/health"), { status: 200, body: { ok: true } });
});
