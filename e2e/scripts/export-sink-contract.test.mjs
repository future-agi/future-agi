import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { handleRequest } from "../stack/webhook-sink/server.mjs";

function call(vendor, path, method, body, headers = {}) {
  const req = Object.assign(new EventEmitter(), {
    url: `/r/contract/as/${vendor}${path}`,
    method,
    headers,
  });
  const res = {
    status: 0,
    raw: "",
    writeHead(s) {
      this.status = s;
    },
    end(s) {
      this.raw = s;
    },
    destroy() {
      assert.fail("unexpected reset");
    },
  };
  handleRequest(req, res);
  if (body !== undefined)
    req.emit(
      "data",
      Buffer.from(typeof body === "string" ? body : JSON.stringify(body)),
    );
  req.emit("end");
  return { status: res.status, body: JSON.parse(res.raw) };
}
// Isolated fixture contract only: a dummy username is not live vendor auth.
const importBatch = [
  {
    event: "request",
    properties: { time: 1700000000, distinct_id: "d", $insert_id: "request-1" },
  },
];
const basic = (username) => ({
  authorization: `Basic ${Buffer.from(`${username}:dummy-password`).toString("base64")}`,
});
const importAnswer = (headers) =>
  call("mixpanel", "/import", "POST", importBatch, headers);
const importSuccess = {
  status: 200,
  body: { code: 200, num_records_imported: 1, status: "OK" },
};

test("Mixpanel import accepts a nonblank dummy username and a shaped event batch", () => {
  assert.deepEqual(importAnswer(basic("e2e-contract-valid")), importSuccess);
});
for (const [label, headers] of [
  ["missing", {}],
  ["empty", basic("")],
  ["spaces", basic("   ")],
  ["tab", basic("\t")],
  ["mixed whitespace", basic(" \r\n\t ")],
]) {
  test(`Mixpanel import refuses a ${label} decoded Basic username`, () => {
    assert.deepEqual(importAnswer(basic("e2e-contract-valid")), importSuccess);
    assert.deepEqual(importAnswer(headers), {
      status: 401,
      body: { code: 401, error: "Invalid credentials", status: "Unauthorized" },
    });
    assert.deepEqual(importAnswer(basic("e2e-contract-valid")), importSuccess);
  });
}

const dd = { "dd-api-key": "e2e-contract-valid" };
const event = {
  event: "request",
  properties: { distinct_id: "d", token: "e2e-contract-valid" },
};

for (const [path, method, body, status] of [
  ["/api/v1/validate", "GET", undefined, 200],
  ["/api/v2/logs", "POST", [{ message: "request" }], 202],
  [
    "/api/v2/series",
    "POST",
    { series: [{ metric: "requests", points: [{ timestamp: 1, value: 1 }] }] },
    202,
  ],
]) {
  test(`Datadog ${path} requires method and nonempty key`, () => {
    assert.equal(call("datadog", path, method, body, dd).status, status);
    assert.equal(call("datadog", path, method, body).status, 403);
    assert.equal(
      call("datadog", path, method, body, { "dd-api-key": " " }).status,
      403,
    );
    assert.equal(call("datadog", path, "DELETE", body, dd).status, 405);
  });
}
for (const body of ["not-json", {}, [null], [{ wrong: "shape" }]]) {
  test(`Datadog logs rejects malformed payload ${JSON.stringify(body)}`, () => {
    assert.equal(call("datadog", "/api/v2/logs", "POST", body, dd).status, 400);
  });
}
for (const path of ["/decide/", "/batch/"]) {
  test(`PostHog ${path} checks credentials, method and shape`, () => {
    const good = { api_key: "e2e-contract-valid", batch: [event] };
    assert.equal(call("posthog", path, "POST", good).status, 200);
    assert.equal(call("posthog", path, "POST", { batch: [event] }).status, 401);
    assert.equal(
      call("posthog", path, "POST", { ...good, api_key: "" }).status,
      401,
    );
    assert.equal(call("posthog", path, "GET", good).status, 405);
    assert.equal(call("posthog", path, "POST", "not-json").status, 400);
  });
}
test("PostHog batch rejects a non-list and malformed events", () => {
  for (const batch of [{}, [null], [{ properties: {} }]])
    assert.equal(
      call("posthog", "/batch/", "POST", {
        api_key: "e2e-contract-valid",
        batch,
      }).status,
      400,
    );
});
test("Mixpanel track requires an event and POST; import requires an array", () => {
  assert.deepEqual(call("mixpanel", "/track", "POST", [event]), {
    status: 200,
    body: 1,
  });
  assert.deepEqual(
    call("mixpanel", "/track", "POST", [{ properties: event.properties }]),
    { status: 200, body: 0 },
  );
  assert.equal(call("mixpanel", "/track", "DELETE", [event]).status, 405);
  assert.equal(
    call(
      "mixpanel",
      "/import",
      "POST",
      {},
      {
        authorization: `Basic ${Buffer.from("e2e-contract-valid:").toString("base64")}`,
      },
    ).status,
    400,
  );
});
