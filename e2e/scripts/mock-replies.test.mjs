import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { handleRequest, parseScoped } from "../stack/mock-llm/server.mjs";

// Socket-free, like scripts/mock-llm.test.mjs; never starts the listener.
function call(method, url, body = "", chunks, headers = {}) {
  const req = Object.assign(new EventEmitter(), { method, url, headers });
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
  handleRequest(req, res);
  for (const chunk of chunks ?? [
    typeof body === "string" ? body : JSON.stringify(body),
  ])
    req.emit("data", Buffer.from(chunk));
  req.emit("end");
  assert.equal(res.ended, true);
  return {
    status: res.status,
    headers: res.headers,
    raw: res.data,
    body:
      res.headers["Content-Type"] === "application/json"
        ? JSON.parse(res.data)
        : null,
  };
}

const messages = [{ role: "user", content: "judge instructions fixture" }];
const route = (scenario, directives = "-") =>
  `/s/${scenario}/${directives}/v1/chat/completions`;
const chat = (url, input = messages, extra = {}) =>
  call("POST", url, { model: "gpt-4o-mini", messages: input, ...extra });
const completion = (content) => ({
  id: "chatcmpl-e2e",
  object: "chat.completion",
  model: "gpt-4o-mini",
  choices: [
    {
      index: 0,
      message: { role: "assistant", content },
      finish_reason: "stop",
    },
  ],
  usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 },
});
function expectContent(response, content) {
  assert.equal(response.status, 200);
  assert.deepEqual(response.headers, { "Content-Type": "application/json" });
  assert.deepEqual(response.body, completion(content));
}
function scenario(t, suffix) {
  const name = `reply-${suffix}`;
  t.after(() => call("DELETE", `/__replies/${name}`));
  return name;
}
function register(name, rules) {
  const response = call("POST", `/__replies/${name}`, rules);
  assert.equal(response.status, 200);
  assert.deepEqual(response.body, { stored: true });
}

// These are new contracts: BASE rejects reply= and has no /__replies routes.
test("named replies preserve raw UTF-8, whitespace and empty content", (t) => {
  const scope = scenario(t, "raw");
  const text = ' \n```json\n{"reason":"café 漢 𝄞"}\n```\r\n';
  const bytes = Buffer.from(text);
  // One-byte chunks split every multibyte character across data events.
  const chunks = Array.from(bytes, (byte) => Buffer.from([byte]));
  const stored = call("POST", `/__replies/${scope}/Judge_1.v2`, "", chunks);
  assert.equal(stored.status, 200);
  assert.deepEqual(stored.body, { stored: true });
  expectContent(chat(route(scope, "reply=Judge_1.v2")), text);

  for (const body of ['{"score":1}', "", "null", "not JSON"]) {
    const replaced = call("POST", `/__replies/${scope}/Judge_1.v2`, body);
    assert.equal(replaced.status, 200);
    assert.deepEqual(replaced.body, { stored: true });
    expectContent(chat(route(scope, "reply=Judge_1.v2")), body);
  }
});

test("reply names are scoped and missing names never fall back to rules or echo", (t) => {
  const first = scenario(t, "names-first");
  const second = scenario(t, "names-second");
  assert.equal(call("POST", `/__replies/${first}/judge`, "first").status, 200);
  register(second, [{ user_contains: messages[0].content, body: "rule" }]);
  for (const url of [route(first, "reply=missing"), route(second, "reply=judge")]) {
    const response = chat(url);
    assert.equal(response.status, 404);
    assert.deepEqual(response.body, { error: "reply not registered" });
  }
  expectContent(chat(route(first, "reply=judge")), "first");
  expectContent(chat(route(second)), "rule");
});

test("reply directives accept bounded names and reject duplicates and exclusive shapes", () => {
  for (const name of ["a", "Judge_1.v2-", "x".repeat(80)]) {
    assert.deepEqual(parseScoped(route("fixture", `reply=${name}`)), {
      scenario: "fixture",
      inner: "/v1/chat/completions",
      latencyMs: 0,
      status: 0,
      reply: name,
    });
  }
  for (const directive of ["reply=", "reply", "reply=a=b", "reply=%20", `reply=${"x".repeat(81)}`]) {
    assert.equal(parseScoped(route("fixture", directive)).invalid, directive);
  }
  for (const [directives, invalid] of [
    ["reply=one,reply=two", "reply=two"],
    ["reply=one,refusal=1", "refusal=1"],
    ["refusal=1,reply=one", "reply=one"],
    ["reply=one,refusal=echo", "refusal=echo"],
    ["refusal=echo,reply=one", "reply=one"],
    ["reply=one,audio=bare", "audio=bare"],
    ["audio=bare,reply=one", "reply=one"],
    ["reply=one,audio=transcript", "audio=transcript"],
    ["audio=transcript,reply=one", "reply=one"],
    ["refusal=1,audio=bare", "audio=bare"],
    ["audio=bare,refusal=1", "refusal=1"],
  ]) {
    assert.equal(parseScoped(route("fixture", directives)).invalid, invalid);
    const response = chat(route("fixture", directives));
    assert.equal(response.status, 400);
    assert.deepEqual(response.body, {
      error: {
        message: `invalid mock-llm directive ${invalid}`,
        type: "invalid_request_error",
      },
    });
  }
});

test("reply stream requests use the existing non-stream-only error even for unknown names", (t) => {
  const scope = scenario(t, "stream");
  assert.equal(call("POST", `/__replies/${scope}/judge`, "answer").status, 200);
  for (const name of ["judge", "unknown"]) {
    const response = chat(route(scope, `reply=${name}`), messages, { stream: true });
    assert.equal(response.status, 400);
    assert.deepEqual(response.body, {
      error: {
        message: `mock-llm directive reply=${name} answers non-stream chat completions only`,
        type: "invalid_request_error",
      },
    });
  }
});

test("named replies retain usage overrides and leave other routes unchanged", (t) => {
  const scope = scenario(t, "options");
  assert.equal(call("POST", `/__replies/${scope}/judge`, "answer").status, 200);
  const response = chat(route(scope, "reply=judge,usage=zero,served=fixture"));
  assert.equal(response.status, 200);
  assert.deepEqual(response.body, {
    ...completion("answer"),
    model: "fixture",
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  });
  const embedding = { input: "embedding fixture" };
  assert.deepEqual(
    call("POST", `/s/${scope}/reply=missing/v1/embeddings`, embedding),
    call("POST", "/v1/embeddings", embedding),
  );
});

test("scoped reply hits and misses record original requests without registration entries", (t) => {
  const scope = scenario(t, "recording");
  assert.equal(call("POST", `/__replies/${scope}/judge`, "answer").status, 200);
  const headers = { "x-fixture": "recorded" };
  const body = { model: "gpt-4o-mini", messages };
  const paths = [route(scope, "reply=judge"), `${route(scope, "reply=missing")}?test=1`];
  assert.equal(call("POST", paths[0], body, undefined, headers).status, 200);
  assert.equal(call("POST", paths[1], body, undefined, headers).status, 404);
  const log = call("GET", `/__requests/${scope}`);
  assert.equal(log.status, 200);
  assert.equal(log.body.scenario, scope);
  assert.deepEqual(
    log.body.requests.map(({ at, ...entry }) => {
      assert.equal(at, new Date(at).toISOString());
      return entry;
    }),
    paths.map((path) => ({ method: "POST", path, headers, body })),
  );
});

test("scoped rules combine all selectors across role text strings and blocks", (t) => {
  const scope = scenario(t, "role-text");
  const system = "system-marker-unique";
  const user = "user-marker-unique";
  const input = [
    { role: "system", content: "system-" },
    { role: "system", content: [{ type: "text", text: "marker-unique" }] },
    { role: "user", content: "user-" },
    { role: "user", content: [
      { type: "text", text: "marker-" },
      { type: "image_url", image_url: { url: "fixture" } },
      { type: "text", text: "unique" },
    ] },
  ];
  register(scope, [
    { system_contains: system, user_contains: user, body: "both selectors" },
    { user_contains: user, body: "user selector" },
    { system_contains: system, body: "system selector" },
  ]);
  expectContent(chat(route(scope), input), "both selectors");
  expectContent(chat(route(scope), input.filter((message) => message.role === "user")), "user selector");
  expectContent(chat(route(scope), input.filter((message) => message.role === "system")), "system selector");
  const other = scenario(t, "role-isolation");
  expectContent(chat(route(other), input), `echo: ${JSON.stringify(input.at(-1).content)}`);
});

test("unscoped rules match judge instructions with a fixed system prompt and record only the winner", (t) => {
  const scope = scenario(t, "unscoped");
  const marker = "unique-judge-instructions-marker";
  register(scope, [{ user_contains: marker, body: '{"improved_prompt":"fixture"}' }]);
  const body = {
    model: "gpt-4o-mini",
    messages: [
      { role: "system", content: "Fixed eval writer system prompt." },
      { role: "user", content: `Improve these judge instructions: ${marker}` },
    ],
  };
  const path = "/v1/chat/completions?fixture=writer";
  const headers = { "x-fixture": "writer" };
  expectContent(call("POST", path, body, undefined, headers), '{"improved_prompt":"fixture"}');
  const log = call("GET", `/__requests/${scope}`);
  assert.equal(log.status, 200);
  assert.deepEqual(log.body.requests.map(({ at, ...entry }) => entry), [
    { method: "POST", path, headers, body },
  ]);
});

test("unscoped matching respects scenario registration order, rule order and replacement position", (t) => {
  const first = scenario(t, "order-z");
  const second = scenario(t, "order-a");
  const marker = "registration-order-marker";
  const input = [{ role: "user", content: marker }];
  register(first, [
    { user_contains: "nonmatching-marker", body: "skip" },
    { user_contains: marker, body: "first rule" },
    { user_contains: marker, body: "later rule" },
  ]);
  register(second, [{ user_contains: marker, body: "second scenario" }]);
  expectContent(chat("/v1/chat/completions", input), "first rule");
  assert.deepEqual(call("GET", `/__requests/${second}`).body.requests, []);
  register(first, [{ user_contains: marker, body: "replacement" }]);
  expectContent(chat("/v1/chat/completions", input), "replacement");
  assert.equal(call("DELETE", `/__replies/${first}`).status, 200);
  register(first, [{ user_contains: marker, body: "re-registered" }]);
  expectContent(chat("/v1/chat/completions", input), "second scenario");
  assert.equal(call("GET", `/__requests/${first}`).body.requests.length, 2);
  assert.equal(call("GET", `/__requests/${second}`).body.requests.length, 1);
});

test("rule lists require known selectors of at least twelve characters and replace atomically", (t) => {
  const scope = scenario(t, "validation");
  const marker = "twelve-chars";
  assert.equal(marker.length, 12);
  register(scope, [{ system_contains: marker, user_contains: marker, body: "kept" }]);
  const input = [
    { role: "system", content: marker },
    { role: "user", content: marker },
  ];
  for (const invalid of [
    "not JSON", "", null, {}, 42,
    [null], [[]], ["rule"], [{ body: "missing selector" }],
    [{ user_contains: "elevenchars", body: "short" }],
    [{ system_contains: "elevenchars", body: "short" }],
    [{ user_contains: marker }],
    [{ user_contains: marker, body: null }],
    [{ user_contains: marker, system_contains: "short", body: "invalid" }],
    [{ system_contains: marker, user_contains: 12, body: "invalid" }],
    [{ user_contains: null, body: "invalid" }],
    [{ user_contains: marker, body: "invalid", extra: "unknown" }],
    [{ user_contains: marker, body: "partial" }, { body: "invalid" }],
  ]) {
    const response = call("POST", `/__replies/${scope}`, invalid);
    assert.equal(response.status, 400);
    assert.deepEqual(response.body, { error: "invalid reply rules" });
    expectContent(chat(route(scope), input), "kept");
  }
  register(scope, [{ user_contains: marker, body: "" }]);
  expectContent(chat(route(scope), input), "");
  register(scope, []);
  expectContent(chat(route(scope), input), `echo: ${marker}`);
});

test("unmatched rules preserve exact echo and ignore other roles and non-text blocks", (t) => {
  const scope = scenario(t, "no-match");
  const marker = "not-user-text-marker";
  register(scope, [{ user_contains: marker, body: "must not appear" }]);
  for (const input of [
    [{ role: "user", content: "ordinary echo" }],
    [{ role: "assistant", content: marker }, { role: "user", content: "ordinary echo" }],
    [{ role: "tool", content: marker }, { role: "user", content: "ordinary echo" }],
    [{ role: "system", content: marker }, { role: "user", content: "ordinary echo" }],
    [{ role: "user", content: [{ type: "image_url", text: marker }] }],
  ]) {
    const last = input.at(-1).content;
    const expected = `echo: ${typeof last === "string" ? last : JSON.stringify(last)}`;
    const unscoped = chat("/v1/chat/completions", input);
    expectContent(unscoped, expected);
    assert.equal(unscoped.raw, JSON.stringify(completion(expected)));
    assert.deepEqual(chat(route(scope), input), unscoped);
  }
  assert.equal(call("GET", `/__requests/${scope}`).body.requests.length, 5);
});

test("rules never override named replies, other directives, other routes or streaming", (t) => {
  const scope = scenario(t, "precedence");
  const baselineStream = chat("/v1/chat/completions", messages, { stream: true });
  const baselineRefusal = chat(route(scope, "refusal=echo"));
  const baselineAudio = chat(route(scope, "audio=transcript"));
  const baselineEmbedding = call("POST", "/v1/embeddings", { input: "fixture" });
  register(scope, [{ user_contains: messages[0].content, body: "matched" }]);
  assert.equal(call("POST", `/__replies/${scope}/judge`, "named").status, 200);
  expectContent(chat(route(scope, "reply=judge")), "named");
  expectContent(chat(route(scope, "lat=0")), `echo: ${messages[0].content}`);
  assert.deepEqual(chat(route(scope, "refusal=echo")), baselineRefusal);
  assert.deepEqual(chat(route(scope, "audio=transcript")), baselineAudio);
  for (const url of [route(scope), "/v1/chat/completions"])
    assert.deepEqual(chat(url, messages, { stream: true }), baselineStream);
  assert.deepEqual(
    call("POST", `/s/${scope}/-/v1/embeddings`, { input: "fixture" }),
    baselineEmbedding,
  );
});

test("deleting replies clears only that scenario's names and rules and preserves logs", (t) => {
  const first = scenario(t, "delete-first");
  const second = scenario(t, "delete-second");
  const marker = "deletion-match-marker";
  const input = [{ role: "user", content: marker }];
  for (const scope of [first, second]) {
    assert.equal(call("POST", `/__replies/${scope}/judge`, scope).status, 200);
    register(scope, [{ user_contains: marker, body: scope }]);
  }
  expectContent(chat(route(first, "reply=judge"), input), first);
  expectContent(chat("/v1/chat/completions", input), first);
  const before = call("GET", `/__requests/${first}`);
  for (let i = 0; i < 2; i++) {
    const deleted = call("DELETE", `/__replies/${first}`);
    assert.equal(deleted.status, 200);
    assert.deepEqual(deleted.body, { cleared: true });
  }
  assert.deepEqual(call("GET", `/__requests/${first}`), before);
  const missing = chat(route(first, "reply=judge"), input);
  assert.equal(missing.status, 404);
  assert.deepEqual(missing.body, { error: "reply not registered" });
  expectContent(chat(route(first), input), `echo: ${marker}`);
  expectContent(chat(route(second, "reply=judge"), input), second);
  expectContent(chat("/v1/chat/completions", input), second);
  assert.equal(call("DELETE", `/__replies/${second}`).status, 200);
  expectContent(chat("/v1/chat/completions", input), `echo: ${marker}`);
});
