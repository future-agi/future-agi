import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { handleRequest } from "../stack/mock-llm/server.mjs";

function call(method, url, raw = "{}") {
  const req = Object.assign(new EventEmitter(), { method, url, headers: {} });
  let body;
  const res = {
    writeHead() {},
    end(text) {
      body = JSON.parse(text);
    },
    destroy() {},
  };
  handleRequest(req, res);
  req.emit("data", Buffer.from(raw));
  req.emit("end");
  return body;
}
test("counter sees unscoped, scoped and invalid inference before response, not inspection reads", () => {
  const before = call("GET", "/__counts").inferenceRequests;
  call("GET", "/__health");
  call("GET", "/v1/models");
  assert.equal(call("GET", "/__counts").inferenceRequests, before);
  call("POST", "/v1/chat/completions", "{");
  call("POST", "/s/count-fixture/-/v1/chat/completions");
  call("POST", "/v1/chat/completions");
  assert.deepEqual(call("GET", "/__counts"), { inferenceRequests: before + 3 });
  assert.deepEqual(call("GET", "/__counts"), { inferenceRequests: before + 3 });
  const marker = `e2e-census-${"a".repeat(24)}`;
  const other = `e2e-census-${"b".repeat(24)}`;
  assert.deepEqual(call("GET", `/__counts/${marker}`), {
    inferenceRequests: 0,
  });
  const raw = JSON.stringify({ messages: [{ role: "user", content: marker }] });
  call("POST", "/v1/chat/completions", raw);
  call("POST", "/s/count-fixture/-/v1/chat/completions", raw);
  assert.deepEqual(call("GET", `/__counts/${marker}`), {
    inferenceRequests: 2,
  });
  assert.deepEqual(call("GET", `/__counts/${other}`), { inferenceRequests: 0 });
});
