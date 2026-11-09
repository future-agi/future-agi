import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { readFileSync } from "node:fs";
import test from "node:test";

const serverURL = new URL("../stack/mock-llm/server.mjs", import.meta.url);
// Red-first safety: never import the old module that listens at evaluation.
assert.match(
  readFileSync(serverURL, "utf8"),
  /export function handleRequest\(/,
);
const { handleRequest } = await import(serverURL.href);

function call(method, url, body = "", chunks) {
  const req = Object.assign(new EventEmitter(), { method, url });
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
  for (const part of chunks ?? [
    typeof body === "string" ? body : JSON.stringify(body),
  ])
    req.emit("data", Buffer.from(part));
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

test("serving health and scalar/batch embeddings are finite nonzero eight-vectors", () => {
  assert.deepEqual(call("GET", "/model/v1/models").body, {
    models: ["text_embedding"],
  });
  for (const text of ["one", ["first", "second"], Array(64).fill("bounded")]) {
    const response = call("POST", "/model/v1/embed", {
      input_type: "text",
      text,
    });
    assert.equal(response.status, 200);
    assert.deepEqual(response.body, {
      embeddings: Array.from(
        { length: Array.isArray(text) ? text.length : 1 },
        () => [0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125],
      ),
    });
  }
});

// The product probes `${MODEL_SERVING_URL}/health` before embedding
// (futureagi/agentic_eval/core/embeddings/serving_client.py _probe). The real
// serving app answers it unconditionally with FastAPI's JSONResponse
// (futureagi/model_serving/app/main.py `@app.get("/health")`): 200,
// application/json, {"status":"healthy"} in compact separators.
test("GET /health emulates the real model-serving health contract byte for byte", async () => {
  const health = call("GET", "/health");
  assert.equal(health.status, 200);
  assert.deepEqual(health.headers, { "Content-Type": "application/json" });
  assert.equal(health.raw, '{"status":"healthy"}');
  const scoped = await scopedCall("GET", "/s/sc-health/-/health");
  assert.equal(scoped.status, 200);
  assert.equal(scoped.raw, health.raw);
  // No collateral change: the mock's own liveness, the method gate and other
  // methods on /health answer exactly as before.
  assert.equal(call("GET", "/__health").raw, '{"ok":true}');
  assert.equal(call("GET", "/v1/chat/completions").status, 405);
  assert.equal(call("GET", "/healthz").status, 405);
  const posted = call("POST", "/health", {});
  assert.equal(posted.status, 404);
  assert.deepEqual(posted.body, { error: { message: "no route /health" } });
});

test("serving rejects invalid shapes, unsupported methods/routes and oversized bodies", () => {
  for (const body of [
    "{",
    "null",
    "42",
    '"x"',
    "[]",
    {},
    { text: "x" },
    { text: "x", input_type: "image" },
    { text: "", input_type: "text" },
    { text: "  ", input_type: "text" },
    { text: [], input_type: "text" },
    { text: ["yes", 1], input_type: "text" },
    { text: [null], input_type: "text" },
    { text: Array(65).fill("x"), input_type: "text" },
    { text: "x", input_type: "text", url: "https://provider.invalid" },
  ]) {
    assert.equal(call("POST", "/model/v1/embed", body).status, 400);
  }
  assert.equal(call("GET", "/model/v1/embed").status, 405);
  assert.equal(call("POST", "/model/v1/models").status, 405);
  for (const path of [
    "/model/v1/embed/image",
    "/model/v1/embed/audio",
    "/model/v1/other",
  ]) {
    assert.equal(call("POST", path, {}).status, 404);
  }
  assert.equal(
    call("POST", "/model/v1/embed", "", [
      "x".repeat(131072),
      "x".repeat(131073),
    ]).status,
    413,
  );
});

test("existing OpenAI model list, chat usage, streaming echo and embedding wire stay exact", () => {
  assert.deepEqual(call("GET", "/v1/models").body, {
    object: "list",
    data: ["gpt-4o-mini", "gpt-4o", "text-embedding-3-small"].map((id) => ({
      id,
      object: "model",
      owned_by: "e2e",
    })),
  });
  const messages = [
    { role: "user", content: "old" },
    { role: "assistant", content: "ignored" },
    { role: "user", content: "hello  world\nagain" },
  ];
  const chat = call("POST", "/v1/chat/completions", {
    model: "turing_flash",
    messages,
  });
  assert.deepEqual(chat.body, {
    id: "chatcmpl-e2e",
    object: "chat.completion",
    model: "turing_flash",
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: "echo: hello  world\nagain" },
        finish_reason: "stop",
      },
    ],
    usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 },
  });
  const stream = call("POST", "/v1/chat/completions", {
    messages,
    stream: true,
  });
  const chunks = stream.raw
    .split("\n\n")
    .filter((line) => line && line !== "data: [DONE]")
    .map((line) => JSON.parse(line.slice(6)));
  assert.equal(
    chunks.map((chunk) => chunk.choices[0]?.delta.content ?? "").join(""),
    chat.body.choices[0].message.content,
  );
  assert.equal(chunks.at(-2).choices[0].finish_reason, "stop");
  assert.deepEqual(chunks.at(-1).usage, {
    prompt_tokens: 7,
    completion_tokens: 7,
    total_tokens: 14,
  });
  assert.ok(stream.raw.endsWith("data: [DONE]\n\n"));
  assert.deepEqual(call("POST", "/v1/embeddings", { input: ["a", "b"] }).body, {
    object: "list",
    model: "text-embedding-3-small",
    data: [0, 1].map((index) => ({
      object: "embedding",
      index,
      embedding: [0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125],
    })),
    usage: { prompt_tokens: 1, total_tokens: 1 },
  });
});

// H2 (e2e/lib/upstream.ts): /s/<scenario>/<directives>/<route>. The scoped routes
// answer asynchronously when delayed, so this helper resolves on res.end.
function scopedCall(method, url, body = "", headers = {}) {
  return new Promise((resolve) => {
    const req = Object.assign(new EventEmitter(), { method, url, headers });
    const started = Date.now();
    const res = {
      status: 0,
      headers: {},
      data: "",
      writeHead(status, h) {
        this.status = status;
        this.headers = h;
      },
      write(data) {
        this.data += data;
      },
      end(data = "") {
        this.data += data;
        resolve({
          status: this.status,
          raw: this.data,
          ms: Date.now() - started,
          body:
            this.headers["Content-Type"] === "application/json"
              ? JSON.parse(this.data)
              : null,
        });
      },
      destroy() {},
    };
    handleRequest(req, res);
    req.emit(
      "data",
      Buffer.from(typeof body === "string" ? body : JSON.stringify(body)),
    );
    req.emit("end");
  });
}

const chatBody = {
  model: "e2e-scoped-model",
  messages: [{ role: "user", content: "scoped hello" }],
};

test("scoped routes answer exactly the unscoped echo, usage 7/7/14 included, streaming too", async () => {
  const plain = call("POST", "/v1/chat/completions", chatBody);
  const scoped = await scopedCall(
    "POST",
    "/s/sc-echo/-/v1/chat/completions",
    chatBody,
  );
  assert.equal(scoped.status, 200);
  assert.deepEqual(scoped.body, plain.body);
  assert.deepEqual(scoped.body.usage, {
    prompt_tokens: 7,
    completion_tokens: 7,
    total_tokens: 14,
  });
  const stream = await scopedCall(
    "POST",
    "/s/sc-echo/lat=1/v1/chat/completions",
    { ...chatBody, stream: true },
  );
  assert.equal(
    stream.raw,
    call("POST", "/v1/chat/completions", { ...chatBody, stream: true }).raw,
  );
  assert.deepEqual(
    (await scopedCall("GET", "/s/sc-echo/-/v1/models")).body,
    call("GET", "/v1/models").body,
  );
});

test("lat=<ms> answers no sooner than the delay; status=<code> answers that status with an error body", async () => {
  const slow = await scopedCall(
    "POST",
    "/s/sc-lat/lat=400/v1/chat/completions",
    chatBody,
  );
  assert.equal(slow.status, 200);
  assert.ok(slow.ms >= 400, `answered after ${slow.ms} ms`);
  const failed = await scopedCall(
    "POST",
    "/s/sc-status/status=503/v1/chat/completions",
    chatBody,
  );
  assert.equal(failed.status, 503);
  assert.deepEqual(failed.body, {
    error: {
      message: "mock-llm scenario sc-status answers 503",
      type: "server_error",
    },
  });
  const both = await scopedCall(
    "POST",
    "/s/sc-both/status=502,lat=150/v1/chat/completions",
    chatBody,
  );
  assert.equal(both.status, 502);
  assert.ok(both.ms >= 150, `answered after ${both.ms} ms`);
});

test("every scoped request is recorded under its scenario only, oldest first", async () => {
  await scopedCall("POST", "/s/sc-rec-a/-/v1/chat/completions", chatBody, {
    authorization: "Bearer sk-a",
  });
  await scopedCall(
    "POST",
    "/s/sc-rec-b/status=500/v1/chat/completions",
    { model: "b" },
    { authorization: "Bearer sk-b" },
  );
  await scopedCall(
    "POST",
    "/s/sc-rec-a/-/v1/chat/completions?x=1",
    "not json",
    { authorization: "Bearer sk-a" },
  );
  const a = call("GET", "/__requests/sc-rec-a").body;
  assert.equal(a.scenario, "sc-rec-a");
  assert.deepEqual(
    a.requests.map(({ method, path, headers, body }) => ({
      method,
      path,
      headers,
      body,
    })),
    [
      {
        method: "POST",
        path: "/s/sc-rec-a/-/v1/chat/completions",
        headers: { authorization: "Bearer sk-a" },
        body: chatBody,
      },
      {
        method: "POST",
        path: "/s/sc-rec-a/-/v1/chat/completions?x=1",
        headers: { authorization: "Bearer sk-a" },
        body: "not json",
      },
    ],
  );
  assert.ok(a.requests.every((r) => !Number.isNaN(Date.parse(r.at))));
  assert.deepEqual(
    call("GET", "/__requests/sc-rec-b").body.requests.map((r) => r.body),
    [{ model: "b" }],
  );
  assert.deepEqual(call("GET", "/__requests/sc-never-used").body, {
    scenario: "sc-never-used",
    requests: [],
  });
  // Unscoped traffic is never recorded.
  call("POST", "/v1/chat/completions", chatBody);
  assert.equal(call("GET", "/__requests/sc-rec-a").body.requests.length, 2);
});

test("malformed directives are refused and not recorded", async () => {
  for (const directives of [
    "lat=abc",
    "lat=120001",
    "status=200",
    "status=600",
    "foo=1",
    "lat=1,lat=2",
    "status=503,status=500",
    "lat",
    "lat=1=2",
    "",
  ]) {
    const url = `/s/sc-bad/${directives}/v1/chat/completions`;
    const res = directives
      ? await scopedCall("POST", url, chatBody)
      : call("POST", url, chatBody);
    assert.equal(res.status, directives ? 400 : 404, directives);
  }
  assert.deepEqual(call("GET", "/__requests/sc-bad").body.requests, []);
});

const frames = (raw) =>
  raw
    .split("\n")
    .filter((line) => line.startsWith("data: ") && !line.includes("[DONE]"))
    .map((line) => JSON.parse(line.slice(6)));

test("Anthropic start and delta carry input and final output usage, including presence modes", () => {
  for (const usage of ["full", "absent", "input", "output", "zero"]) {
    const path = `/s/anthropic-${usage}/usage=${usage}/v1/messages`;
    const ordinary = call("POST", path, {
      model: "claude-fixture",
      messages: [{ role: "user", content: "hello" }],
    });
    const stream = frames(
      call("POST", path, { model: "claude-fixture", stream: true }).raw,
    );
    assert.deepEqual(
      stream.map((e) => e.type),
      [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
      ],
    );
    const start = stream[0].message,
      terminal = stream.at(-2);
    assert.equal(Object.hasOwn(start, "usage"), usage !== "absent");
    assert.equal(
      Object.hasOwn(terminal, "usage"),
      ["full", "output", "zero"].includes(usage),
    );
    if (["full", "input", "zero"].includes(usage))
      assert.equal(start.usage.input_tokens, usage === "zero" ? 0 : 7);
    if (terminal.usage)
      assert.equal(terminal.usage.output_tokens, usage === "zero" ? 0 : 7);
    assert.equal(Object.hasOwn(ordinary.body, "usage"), usage !== "absent");
  }
});

test("Gemini native usage, thinking-only MAX_TOKENS, and images on both transports", () => {
  for (const action of ["generateContent", "streamGenerateContent"]) {
    for (const usage of ["full", "absent", "input", "output", "zero"]) {
      const response = call(
        "POST",
        `/s/gemini-${action}-${usage}/usage=${usage}/v1beta/models/gemini-fixture:${action}`,
        { contents: [] },
      );
      const body = action.startsWith("stream")
        ? frames(response.raw)[0]
        : response.body;
      assert.equal(Object.hasOwn(body, "usageMetadata"), usage !== "absent");
      if (usage === "input")
        assert.deepEqual(body.usageMetadata, { promptTokenCount: 7 });
      if (usage === "output")
        assert.deepEqual(body.usageMetadata, { candidatesTokenCount: 7 });
    }
    const thought = call(
      "POST",
      `/s/thinking-${action}/thinking=1/v1beta/models/gemini-fixture:${action}`,
      {},
    );
    const b = thought.body ?? frames(thought.raw)[0];
    assert.equal(b.candidates[0].finishReason, "MAX_TOKENS");
    assert.deepEqual(b.candidates[0].content.parts, []);
    assert.deepEqual(b.usageMetadata, {
      promptTokenCount: 7,
      candidatesTokenCount: 0,
      thoughtsTokenCount: 7,
      totalTokenCount: 14,
    });
    const image = call("POST", `/v1beta/models/gemini-image:${action}`, {});
    assert.equal(
      (image.body ?? frames(image.raw)[0]).candidates[0].content.parts[0]
        .inlineData.mimeType,
      "image/png",
    );
  }
});

test("image chat, Lyria clip/pro, served-router cost, overrun and per-attempt failure script", () => {
  for (const stream of [false, true]) {
    const image = call("POST", "/s/image-chat/image=1/v1/chat/completions", {
      stream,
    });
    const images = stream
      ? frames(image.raw).find((f) => f.choices[0]?.delta.images).choices[0]
          .delta.images
      : image.body.choices[0].message.images;
    assert.match(images[0].image_url.url, /^data:image\/png;base64,/);
    const router = call(
      "POST",
      "/s/router/cost=42.123456,served=served-fixture/v1/chat/completions",
      { stream, model: "dynamic-router" },
    );
    const terminal = stream ? frames(router.raw).at(-1) : router.body;
    assert.equal(terminal.model, "served-fixture");
    assert.equal(terminal.usage.cost, 42.123456);
    assert.equal(terminal.usage.prompt_tokens, 7);
    for (const usage of ["absent", "input", "output", "zero"]) {
      const partial = call(
        "POST",
        `/s/chat-${usage}/usage=${usage}/v1/chat/completions`,
        { stream },
      );
      const envelope = stream ? frames(partial.raw).at(-1) : partial.body;
      assert.equal(Object.hasOwn(envelope, "usage"), usage !== "absent");
      if (usage === "input")
        assert.deepEqual(envelope.usage, { prompt_tokens: 7 });
      if (usage === "output")
        assert.deepEqual(envelope.usage, { completion_tokens: 7 });
    }
  }
  for (const model of ["lyria-clip", "lyria-pro"]) {
    const audio = call(
      "POST",
      `/v1/projects/fixture/locations/local/publishers/google/models/${model}:predict`,
      { instances: [{ prompt: "fixture" }] },
    );
    assert.equal(audio.body.predictions[0].mimeType, "audio/wav");
    assert.equal(audio.body.usage.generations, 1);
    assert.equal(
      Buffer.from(audio.body.predictions[0].audioContent, "base64")
        .subarray(0, 4)
        .toString(),
      "RIFF",
    );
  }
  const path = "/s/attempt-failure/failures=1/v1/chat/completions";
  assert.equal(call("POST", path, {}).status, 503);
  assert.equal(call("POST", path, {}).status, 200);
  assert.equal(
    call("GET", "/__requests/attempt-failure").body.requests.length,
    2,
  );
  for (const directives of [
    "usage=unknown",
    "cost=-1",
    "failures=101",
    "thinking=2",
    "served=bad!",
    "cost=1,cost=2",
  ]) {
    assert.equal(
      call("POST", `/s/invalid/${directives}/v1/chat/completions`, {}).status,
      400,
    );
  }
});

test("router served models preserve provider-qualified identifiers without changing route segments", () => {
  const r = call(
    "POST",
    "/s/qualified-router/served=openai%2Fgpt-4o,cost=0.00021/v1/chat/completions",
    {},
  );
  assert.equal(r.body.model, "openai/gpt-4o");
  assert.equal(r.body.usage.cost, 0.00021);
  for (const served of [
    "bad%ZZ",
    "https%3A%2F%2Fvendor",
    "%2Fleading",
    "a%2F%2Fb",
  ])
    assert.equal(
      call("POST", `/s/invalid-served/served=${served}/v1/chat/completions`, {})
        .status,
      400,
    );
});
