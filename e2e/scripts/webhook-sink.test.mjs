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

function call(method, url, body = "", headers = {}, { reset = false } = {}) {
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
  if (reset) {
    assert.equal(res.status, 0, "a stopped channel writes no reply");
    return { status: 0, reset: true };
  }
  return {
    status: res.status,
    body: JSON.parse(res.data),
    type: res.headers["Content-Type"],
    raw: res.data,
  };
}
const listed = (channel) => call("GET", `/__received/${channel}`).body;
const pick = ({ status, body }) => ({ status, body });

test("records every request to a channel, oldest first, and answers 200", () => {
  const event = { event: "batch.completed", data: { batch_id: "b-1" } };
  assert.deepEqual(
    pick(
      call("POST", "/r/ch-rec/hook", event, {
        "x-agentcc-signature": "sha256=ab",
      }),
    ),
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
  assert.deepEqual(pick(call("GET", "/health")), {
    status: 200,
    body: { ok: true },
  });
});

test("as/<vendor> answers each export route as the vendor documents it", () => {
  const answers = [
    ["/r/ch-dd/as/datadog/api/v1/validate", "GET", 200, { valid: true }],
    ["/r/ch-dd/as/datadog/api/v2/logs", "POST", 202, {}],
    ["/r/ch-dd/as/datadog/api/v2/series", "POST", 202, { errors: [] }],
    [
      "/r/ch-ph/as/posthog/decide/?v=3",
      "POST",
      200,
      { featureFlags: {}, sessionRecording: false },
    ],
    ["/r/ch-ph/as/posthog/batch/", "POST", 200, { status: 1 }],
  ];
  for (const [path, method, status, body] of answers) {
    assert.deepEqual(
      pick(
        call(
          method,
          path,
          path.includes("/logs")
            ? [{ message: "x" }]
            : path.includes("/series")
              ? { series: [] }
              : { api_key: "e2e-good-key", batch: [] },
          { "dd-api-key": "e2e-good-key" },
        ),
      ),
      { status, body },
      path,
    );
  }
  const track = call("POST", "/r/ch-mp/as/mixpanel/track", [
    { event: "e", properties: { token: "t" } },
  ]);
  assert.equal(track.status, 200);
  assert.equal(track.type, "text/plain");
  assert.equal(track.raw, "1");
  const imported = (n) =>
    Array.from({ length: n }, (_, i) => ({
      event: "e",
      properties: {
        token: "t",
        time: 1700000000 + i,
        distinct_id: "d",
        $insert_id: `id-${i}`,
      },
    }));
  const secret = {
    authorization: `Basic ${Buffer.from("s3cret:").toString("base64")}`,
  };
  assert.deepEqual(
    pick(
      call("POST", "/r/ch-mp/as/mixpanel/import?strict=1", imported(2), secret),
    ),
    { status: 200, body: { code: 200, num_records_imported: 2, status: "OK" } },
  );
  assert.deepEqual(
    listed("ch-dd").requests.map((r) => [r.method, r.path, r.status]),
    [
      ["GET", "/r/ch-dd/as/datadog/api/v1/validate", 200],
      ["POST", "/r/ch-dd/as/datadog/api/v2/logs", 202],
      ["POST", "/r/ch-dd/as/datadog/api/v2/series", 202],
    ],
  );
});

test("as/<vendor> answers 404 off the vendor's routes, and fail/<n> still comes first", () => {
  for (const path of [
    "/r/ch-off/as/datadog/api/v1/logs",
    "/r/ch-off/as/mixpanel/engage",
    "/r/ch-off/as/posthog/capture/",
    "/r/ch-off/as/datadog",
  ]) {
    assert.equal(call("POST", path).status, 404, path);
  }
  // The 404s are recorded: what the product sent stays readable.
  assert.equal(listed("ch-off").requests.length, 4);
  for (const path of [
    "/r/ch-bad/as/pagerduty/x",
    "/r/ch-bad/fail/1/as/datadog/api/v2/logs",
  ]) {
    assert.equal(call("POST", path).status, 404, path);
  }
  assert.equal(listed("ch-bad").requests.length, 0);
  assert.deepEqual(
    [1, 2].map(
      () =>
        call("POST", "/r/ch-ff/as/mixpanel/fail/1/track", [
          { event: "e", properties: { token: "e2e-good-key" } },
        ]).status,
    ),
    [500, 200],
  );
  assert.equal(call("POST", "/r/ch-assets/assets/x").status, 200);
});

test("as/mixpanel answers 0 to a /track batch with no valid event", () => {
  for (const body of [
    [{ event: "e" }],
    [{ event: "e", properties: { token: "" } }],
    [],
  ]) {
    const track = call("POST", "/r/ch-mp0/as/mixpanel/track", body);
    assert.deepEqual([track.status, track.raw], [200, "0"]);
  }
  const some = call("POST", "/r/ch-mp0/as/mixpanel/track", [
    { event: "e" },
    { event: "e", properties: { token: "t" } },
  ]);
  assert.deepEqual([some.status, some.raw], [200, "1"]);
});

test("as/mixpanel /import needs the secret and every documented property", () => {
  const event = (props) => ({
    event: "e",
    properties: {
      time: 1700000000,
      distinct_id: "d",
      $insert_id: "a-1",
      ...props,
    },
  });
  assert.deepEqual(
    pick(call("POST", "/r/ch-imp/as/mixpanel/import", [event({})])),
    {
      status: 401,
      body: { code: 401, error: "Invalid credentials", status: "Unauthorized" },
    },
  );
  const auth = {
    authorization: `Basic ${Buffer.from("s3cret:").toString("base64")}`,
  };
  const answer = call(
    "POST",
    "/r/ch-imp/as/mixpanel/import",
    [
      event({}),
      event({ $insert_id: undefined }),
      event({ $insert_id: "not valid!" }),
      event({ time: "now" }),
      event({ distinct_id: "" }),
    ],
    auth,
  );
  assert.equal(answer.status, 400);
  assert.equal(answer.body.num_records_imported, 1);
  assert.deepEqual(
    answer.body.failed_records.map((r) => [r.index, r.field]),
    [
      [1, "properties.$insert_id"],
      [2, "properties.$insert_id"],
      [3, "properties.time"],
      [4, "properties.distinct_id"],
    ],
  );
});

test("as/linear answers Linear's GraphQL API for the key it was sent", () => {
  const gql = (query, variables, key = "lin_api_e2e") =>
    call(
      "POST",
      "/r/ch-ln/as/linear/graphql",
      { query, variables },
      key ? { authorization: key, host: "hooks.test:8080" } : {},
    );
  const refused = gql("query { teams { nodes { id name } } }", undefined, "");
  assert.equal(refused.status, 400);
  assert.equal(refused.body.errors[0].extensions.code, "AUTHENTICATION_ERROR");

  const team = { id: "e2e-team-0001", name: "E2E Team", key: "E2E" };
  assert.deepEqual(
    pick(gql("query { teams { nodes { id name } } viewer { id name email } }")),
    {
      status: 200,
      body: {
        data: {
          teams: { nodes: [team] },
          viewer: {
            id: "e2e-viewer",
            name: "E2E Viewer",
            email: "e2e-viewer@e2e.test",
          },
        },
      },
    },
  );
  assert.deepEqual(gql("query { teams { nodes { id name key } } }").body, {
    data: { teams: { nodes: [team] } },
  });

  const create = (variables, key) =>
    gql(
      "mutation CreateIssue($teamId: String!, $title: String!) { issueCreate(input: {}) { success issue { id identifier url title } } }",
      variables,
      key,
    );
  assert.deepEqual(create({ teamId: "other", title: "t" }).body, {
    data: { issueCreate: { success: false, issue: null } },
  });
  const first = create(
    { teamId: team.id, title: "[E2E-1] Timeout" },
    "lin_api_a",
  );
  assert.deepEqual(first.body, {
    data: {
      issueCreate: {
        success: true,
        issue: {
          id: "e2e-issue-E2E-1",
          identifier: "E2E-1",
          url: "http://hooks.test:8080/r/linear-issues/E2E-1",
          title: "[E2E-1] Timeout",
        },
      },
    },
  });
  // Each key numbers its own issues.
  assert.equal(
    create({ teamId: team.id, title: "x" }, "lin_api_a").body.data.issueCreate
      .issue.identifier,
    "E2E-2",
  );
  assert.equal(
    create({ teamId: team.id, title: "x" }, "lin_api_b").body.data.issueCreate
      .issue.identifier,
    "E2E-1",
  );

  assert.deepEqual(
    gql("query Issue($id: String!) { issue(id: $id) { id } }", { id: "E2E-1" })
      .body,
    { data: { issue: { id: "e2e-issue-E2E-1" } } },
  );
  assert.deepEqual(
    gql(
      "mutation CreateComment($issueId: String!, $body: String!) { commentCreate(input: {}) { success comment { id url } } }",
      { issueId: "e2e-issue-E2E-1", body: "b" },
    ).body,
    {
      data: {
        commentCreate: {
          success: true,
          comment: {
            id: "e2e-comment-e2e-issue-E2E-1",
            url: "http://hooks.test:8080/r/linear-issues/E2E-1#comment",
          },
        },
      },
    },
  );
  assert.equal(gql("query { somethingElse }").status, 400);
  assert.equal(call("POST", "/r/ch-ln/as/linear/other", {}).status, 404);
  assert.deepEqual(
    listed("ch-ln").requests.map((r) => r.status),
    [400, 200, 200, 200, 200, 200, 200, 200, 200, 400, 404],
  );
});

test("a stopped channel resets every connection and records it, until it is started again", () => {
  assert.deepEqual(pick(call("PUT", "/__stopped/ch-stop")), {
    status: 200,
    body: { channel: "ch-stop", stopped: true },
  });
  assert.deepEqual(
    call(
      "POST",
      "/r/ch-stop/as/posthog/batch/",
      { batch: [] },
      {},
      { reset: true },
    ),
    { status: 0, reset: true },
  );
  // Other channels keep answering.
  assert.equal(
    call("POST", "/r/ch-still-up/as/posthog/batch/", {
      api_key: "e2e-good-key",
      batch: [],
    }).status,
    200,
  );
  assert.deepEqual(pick(call("DELETE", "/__stopped/ch-stop")), {
    status: 200,
    body: { channel: "ch-stop", stopped: false },
  });
  assert.equal(
    call("POST", "/r/ch-stop/as/posthog/batch/", {
      api_key: "e2e-good-key",
      batch: [],
    }).status,
    200,
  );
  assert.deepEqual(
    listed("ch-stop").requests.map((r) => r.status),
    [0, 200],
  );
});

test("a refused credential is refused on its vendor's every route, and the refusal is recorded", () => {
  // The prefix lib/export-sinks.ts mints refused credentials with.
  const bad = "e2e-refused-ref-1";
  const good = "e2e-good-ref-1";
  const datadog = (route, key) =>
    pick(
      call(
        route.endsWith("validate") ? "GET" : "POST",
        `/r/ch-ref/as/datadog${route}`,
        route.endsWith("logs") ? [] : { series: [] },
        { "dd-api-key": key },
      ),
    );
  for (const route of ["/api/v1/validate", "/api/v2/logs", "/api/v2/series"]) {
    assert.deepEqual(datadog(route, bad), {
      status: 403,
      body: { errors: ["Forbidden"] },
    });
  }
  assert.deepEqual(
    ["/api/v1/validate", "/api/v2/logs", "/api/v2/series"].map(
      (route) => datadog(route, good).status,
    ),
    [200, 202, 202],
  );
  for (const route of ["/decide/", "/batch/"]) {
    assert.equal(
      call("POST", `/r/ch-ref-ph/as/posthog${route}`, {
        api_key: bad,
        batch: [],
      }).status,
      401,
      route,
    );
    assert.equal(
      call("POST", `/r/ch-ref-ph/as/posthog${route}`, {
        api_key: good,
        batch: [],
      }).status,
      200,
      route,
    );
  }
  const track = (token) =>
    call("POST", "/r/ch-ref-mp/as/mixpanel/track", [
      { event: "e", properties: { token } },
    ]).raw;
  assert.deepEqual([track(bad), track(good)], ["0", "1"]);
  const basic = (user) => ({
    authorization: `Basic ${Buffer.from(`${user}:`).toString("base64")}`,
  });
  const event = {
    event: "e",
    properties: { time: 1700000000, distinct_id: "d", $insert_id: "a-1" },
  };
  assert.deepEqual(
    [bad, good].map(
      (user) =>
        call("POST", "/r/ch-ref-mp/as/mixpanel/import", [event], basic(user))
          .status,
    ),
    [401, 200],
  );
  assert.deepEqual(
    listed("ch-ref").requests.map((r) => [r.headers["dd-api-key"], r.status]),
    [
      [bad, 403],
      [bad, 403],
      [bad, 403],
      [good, 200],
      [good, 202],
      [good, 202],
    ],
  );
});

test("a revoked credential is refused on every channel until it is restored", () => {
  const key = "e2e-dd-key-rev-1";
  const other = "e2e-dd-key-rev-2";
  const datadog = (channel, k) =>
    call("POST", `/r/${channel}/as/datadog/api/v2/logs`, [], {
      "dd-api-key": k,
    }).status;
  assert.deepEqual(
    [datadog("ch-rev-a", key), datadog("ch-rev-b", key)],
    [202, 202],
  );
  assert.deepEqual(pick(call("PUT", `/__revoked/${key}`)), {
    status: 200,
    body: { credential: key, revoked: true },
  });
  assert.deepEqual(
    [
      datadog("ch-rev-a", key),
      datadog("ch-rev-b", key),
      datadog("ch-rev-a", other),
    ],
    [403, 403, 202],
  );
  assert.equal(
    call("POST", "/r/ch-rev-ph/as/posthog/batch/", { api_key: key, batch: [] })
      .status,
    401,
  );
  assert.equal(
    call("POST", "/r/ch-rev-mp/as/mixpanel/track", [
      { event: "e", properties: { token: key } },
    ]).raw,
    "0",
  );
  assert.deepEqual(pick(call("DELETE", `/__revoked/${key}`)), {
    status: 200,
    body: { credential: key, revoked: false },
  });
  assert.deepEqual(
    [datadog("ch-rev-a", key), datadog("ch-rev-b", key)],
    [202, 202],
  );
  assert.equal(
    call("POST", "/r/ch-rev-mp/as/mixpanel/track", [
      { event: "e", properties: { token: key } },
    ]).raw,
    "1",
  );
  for (const [method, path] of [
    ["PUT", "/__revoked/"],
    ["PUT", "/__revoked/bad key"],
    ["PUT", `/__revoked/${key}/x`],
    ["GET", `/__revoked/${key}`],
  ]) {
    assert.equal(call(method, path).status, 404, `${method} ${path}`);
  }
});
