import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { registerHooks } from "node:module";
import { handleRequest } from "../stack/mock-llm/server.mjs";
import test from "node:test";
import ts from "typescript";

// As in api-client.test.mjs: import the real TS module, transpiled on load.
const libModule = new URL("../lib/typesafe-live.ts", import.meta.url).href;
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== libModule) return loaded;
    return {
      ...loaded,
      format: "module",
      source: ts.transpileModule(String(loaded.source), {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
      }).outputText,
    };
  },
});
const lib = await import(libModule);

const PROVIDER = "typesafe";
// The mock's own serialization (e2e/stack/mock-llm/server.mjs builds model,
// answers, usage in that order and writes JSON.stringify of it).
const mockText = (answer) => JSON.stringify({
  model: "jev-latest", answers: { resolved: answer }, usage: { input_tokens: 37, output_tokens: 0 },
});
function call(text, extra = {}) {
  let body = text;
  try { body = JSON.parse(text); } catch { /* keep text */ }
  return {
    label: "c", request: { route: "/v1/systemone", model: "typesafe/jev-latest", body: { questions: { resolved: { type: "noul" } } } }, status: 200,
    headers: { "x-agentcc-provider": PROVIDER, "x-agentcc-model-used": body?.model, ...(extra.headers ?? {}) },
    body, text, logRow: "not checked", ...extra,
  };
}

test("expected cost uses the gateway's per-token float (inputPerMTok / 1e6) and %.6f", () => {
  // Go: fmt.Sprintf("%.6f", float64(250)*(0.042/1_000_000)) == "0.000011"; 250 × 0.042e-6 rounds to 0.000010.
  assert.equal(lib.expectedCost(250), "0.000011");
  assert.equal(lib.expectedCost(750), "0.000032");
  assert.equal(lib.expectedCost(37), "0.000002");
  assert.equal(lib.expectedCost(20_000), "0.000840");
});

test("upstream totals count every 200, also from a call made expecting no upstream", () => {
  const counters = { upstreamCalls: 0, upstreamInputTokens: 0 };
  lib.countUpstream(counters, call(mockText({ type: "noul", noul: 0.9 })), false);
  lib.countUpstream(counters, call('{"error":{}}', { status: 429 }), false);
  lib.countUpstream(counters, call('{"error":{}}', { status: 400, headers: { "x-agentcc-upstream-status": "400" } }), true);
  assert.deepEqual(counters, { upstreamCalls: 2, upstreamInputTokens: 37 });
});

test("the 20k-token state stays inside the product's Jev input limit", () => {
  const state = lib.largeState(20_000);
  // >= 20k tokens for a tokenizer at up to 5.5 characters per token...
  assert.ok(state.length >= 20_000 * 5.5, `${state.length} characters`);
  // ...and inside futureagi/ee/jev/mapping.py check_input_size
  // (characters / 4 <= JEV_MAX_STATE_TOKENS = 30,000), with a margin.
  assert.ok(state.length / 4 <= 28_000, `${state.length / 4} estimated tokens`);
});

test("a failed key rotate stops the run", () => {
  assert.equal(lib.rotateOutcome(200, { result: { gateway_synced: true } }).ok, true);
  assert.equal(lib.rotateOutcome(200, { result: { gateway_synced: false } }).ok, false);
  assert.equal(lib.rotateOutcome(500, { result: { gateway_synced: true } }).ok, false);
  assert.equal(lib.rotateOutcome(undefined, undefined).ok, false);
  assert.equal(lib.rotateOutcome(200, {}).ok, false);
});

test("the readiness check wants the real key on the gateway, not a placeholder", () => {
  // /-/orgs/configs masks keys like agentcc-gateway's redactAPIKey.
  const configs = (masked) => ({ data: { org: { providers: { typesafe: { api_key: masked } } } } });
  const real = "e2e-decision-typesafe-key";
  assert.equal(lib.redactedKey(real), "e2e-deci****-key");
  assert.equal(lib.redactedKey("short-key"), "****");
  assert.equal(lib.gatewayHoldsKey(configs("e2e-deci****-key"), "org", "typesafe", real), true);
  assert.equal(lib.gatewayHoldsKey(configs(lib.redactedKey("ts-placeholder-0-mwgabc")), "org", "typesafe", real), false);
  assert.equal(lib.gatewayHoldsKey({ data: {} }, "org", "typesafe", real), false);
  assert.equal(lib.gatewayHoldsKey(configs("****"), "org", "typesafe", ""), false);
});

test("Case 3 dry run: the body must be the mock's bytes, not just equal JSON", () => {
  const exact = call(mockText({ type: "noul", noul: 0.9 }));
  assert.deepEqual(lib.answerShape([exact], { dryRun: true, provider: PROVIDER }), { fails: [], notes: [] });
  // Same JSON value, keys re-ordered: a gateway that re-marshals the body.
  const remarshalled = call(JSON.stringify({ answers: { resolved: { noul: 0.9, type: "noul" } }, model: "jev-latest",
    usage: { input_tokens: 37, output_tokens: 0 } }));
  assert.ok(lib.answerShape([remarshalled], { dryRun: true, provider: PROVIDER }).fails.length > 0);
  const spaced = call(JSON.stringify(JSON.parse(mockText({ type: "noul", noul: 0.9 })), null, 1));
  assert.ok(lib.answerShape([spaced], { dryRun: true, provider: PROVIDER }).fails.length > 0);
});

// Exercise the real import-safe mock handler, without a socket, rather than
// inventing a successful response or calling the helper to build its own oracle.
const questions = {
  noul: { resolved: { type: "noul", instructions: "Was a follow-up promised?" } },
  choice: { sentiment: { type: "choice", instructions: "Sentiment?",
    criteria: { positive: "satisfied or thankful", neutral: "matter of fact", negative: "frustrated or upset" } } },
  score: { quality: { type: "score", instructions: "Reply quality?", criteria: ["poor", "fair", "good", "excellent"] } },
};
const questionCases = [
  ["noul", questions.noul], ["choice", questions.choice], ["score", questions.score],
  ["all three", { ...questions.noul, ...questions.choice, ...questions.score }],
];
function mockPassCall(label, asked) {
  const body = { model: "jev-latest", state: "e2e-decision:pass", questions: asked };
  const req = Object.assign(new EventEmitter(), { method: "POST", url: "/v1/systemone",
    headers: { authorization: `Bearer ${["e2e", "decision", "typesafe", "key"].join("-")}` } });
  let status, text;
  handleRequest(req, {
    writeHead(code) { status = code; },
    end(value) { text = value; },
    destroy() { throw new Error("mock handler destroyed the response"); },
  });
  req.emit("data", Buffer.from(JSON.stringify(body)));
  req.emit("end");
  assert.equal(status, 200, `real mock handler accepted ${label}`);
  assert.equal(typeof text, "string");
  return call(text, { label, status, request: { route: "/v1/systemone", model: "typesafe/jev-latest",
    body: { ...body, model: "typesafe/jev-latest" } } });
}
const dryOptions = { dryRun: true, provider: PROVIDER };
for (const [index, [name]] of questionCases.entries()) {
  test(`Case 3 dry run: ${name} is checked independently, including every answer value and raw byte`, () => {
    const calls = questionCases.map(([label, asked]) => mockPassCall(label, asked));
    const original = calls[index];
    assert.deepEqual(lib.answerShape(calls, dryOptions), { fails: [], notes: [] });
    assert.deepEqual(lib.answerShape([original], dryOptions), { fails: [], notes: [] });
    const rejected = (changed, detail) => {
      const mutated = calls.map((c, i) => i === index ? changed : c);
      assert.ok(lib.answerShape(mutated, dryOptions).fails.some(f => f.startsWith(`${name}:`)), `${name}: ${detail}`);
    };
    const changedBody = (mutate, detail) => {
      const body = structuredClone(original.body);
      mutate(body);
      rejected({ ...original, body, text: JSON.stringify(body) }, detail);
    };
    // Mutate only this call at its real matrix position. In particular, a good
    // first/noul answer must not hide changed choice, score or combined answers.
    for (const [id, answer] of Object.entries(original.body.answers)) {
      const valueField = answer.type;
      changedBody(b => { b.answers[id][valueField] = answer.type === "choice" ? "positive" : 0.1; }, `${id} changed ${valueField}`);
      for (const field of Object.keys(answer)) {
        changedBody(b => { delete b.answers[id][field]; }, `${id} missing ${field}`);
      }
      changedBody(b => { delete b.answers[id]; }, `missing answer ${id}`);
    }
    changedBody(b => { b.answers.unasked = { type: "noul", noul: 0.9 }; }, "extra answer");
    const { model, answers, usage } = original.body;
    rejected({ ...original, text: JSON.stringify({ usage, answers, model }) }, "reordered JSON");
    rejected({ ...original, text: JSON.stringify(original.body, null, 2) }, "reserialized whitespace");
    rejected({ ...original, text: undefined }, "missing raw bytes");
    rejected({ ...original, status: 500 }, "non-200 status");
    rejected({ ...original, headers: { ...original.headers, "x-agentcc-model-used": "wrong-model" } }, "wrong resolved model");
  });
}

test("Case 3 cannot pass an empty batch or a dry-run request without a question oracle", () => {
  assert.ok(lib.answerShape([], dryOptions).fails.length > 0);
  const original = mockPassCall("missing questions", questions.noul);
  assert.ok(lib.answerShape([{ ...original, request: { ...original.request, body: {} } }], dryOptions).fails
    .some(f => /no mock pass oracle/.test(f)));
});

test("Case 3 live: every individual type and combined call reports the missing raw-upstream comparison", () => {
  const calls = questionCases.map(([label, asked]) => mockPassCall(label, asked));
  const result = lib.answerShape(calls, { dryRun: false, provider: PROVIDER });
  assert.deepEqual(result.fails, []);
  assert.equal(result.notes.length, 4);
  for (const [label] of questionCases) {
    assert.ok(result.notes.some(n => n.startsWith(`${label}:`) && n.includes("unchanged live answer NOT PROVEN")), label);
  }
});

test("Case 3 live: vendor fields are a note, missing fields and gateway fields fail", () => {
  const withId = call(JSON.stringify({ id: "r-1", model: "jev-1.14.0", answers: { q: { type: "noul", noul: 0.5 } },
    usage: { input_tokens: 40, output_tokens: 0 } }));
  const r = lib.answerShape([withId], { dryRun: false, provider: PROVIDER });
  assert.deepEqual(r.fails, []);
  assert.equal(r.notes.length, 2);
  assert.ok(r.notes.some(n => /\bid\b/.test(n)));
  assert.ok(r.notes.some(n => /unchanged live answer NOT PROVEN/.test(n)));
  const injected = call(JSON.stringify({ model: "jev-1.14.0", answers: {}, usage: {}, x_agentcc_cost: "0.1" }));
  assert.ok(lib.answerShape([injected], { dryRun: false, provider: PROVIDER }).fails.some(f => /x_agentcc_cost/.test(f)));
  const missing = call(JSON.stringify({ model: "jev-1.14.0", answers: {} }));
  assert.ok(lib.answerShape([missing], { dryRun: false, provider: PROVIDER }).fails.some(f => /usage/.test(f)));
  const wrongHeader = call(JSON.stringify({ model: "jev-1.14.0", answers: {}, usage: {} }), { headers: { "x-agentcc-provider": "openai" } });
  assert.ok(lib.answerShape([wrongHeader], { dryRun: false, provider: PROVIDER }).fails.length > 0);
});

function bigCall(logRow) {
  const usage = { input_tokens: 21_000, output_tokens: 0 };
  return call(JSON.stringify({ model: "jev-1.14.0", answers: {}, usage }), {
    label: "big", request: { route: "/v1/systemone", model: "typesafe/jev-1.14.0", body: {} },
    headers: { "x-agentcc-provider": PROVIDER, "x-agentcc-cost": "0.000882" }, expectedCost: lib.expectedCost(21_000), logRow,
  });
}
const goodRow = { count: 1, model: "typesafe/jev-1.14.0", resolved_model: "jev-1.14.0", provider: PROVIDER,
  input_tokens: 21_000, output_tokens: 0, cost: "0.000882", endpoint_type: "decision" };

test("Case 7b: a 200 large call with no Request Logs row is an issue", () => {
  const issues = lib.largeCallIssues(bigCall("no row"), { dryRun: false, provider: PROVIDER, minTokens: 20_000 });
  assert.ok(issues.some(i => /no row/.test(i)), JSON.stringify(issues));
});

test("Case 7b: the found row gets Case 4's checks (count, endpoint type, tokens, model, cost)", () => {
  assert.deepEqual(lib.largeCallIssues(bigCall(goodRow), { dryRun: false, provider: PROVIDER, minTokens: 20_000 }), []);
  for (const [field, value] of [["count", 2], ["endpoint_type", "chat"], ["input_tokens", 37], ["model", "typesafe/jev-latest"]]) {
    const issues = lib.largeCallIssues(bigCall({ ...goodRow, [field]: value }), { dryRun: false, provider: PROVIDER, minTokens: 20_000 });
    assert.ok(issues.length > 0, `${field}=${value} must be an issue`);
  }
});

test("Case 6d dry run: only the mock's refusal, byte for byte, passes", () => {
  assert.equal(lib.refusalVerdict(call(mockText({ type: "refusal" })), { dryRun: true }), "PASS");
  assert.equal(lib.refusalVerdict(call(mockText({ type: "noul", noul: 0.1 })), { dryRun: true }), "FAIL");
  assert.equal(lib.refusalVerdict(call(JSON.stringify({ answers: { resolved: { type: "refusal" } }, model: "jev-latest",
    usage: { input_tokens: 37, output_tokens: 0 } })), { dryRun: true }), "FAIL");
  // Live: a refusal cannot be forced with synthetic text.
  assert.equal(lib.refusalVerdict(call(mockText({ type: "noul", noul: 0.1 })), { dryRun: false }), "NOTE");
  assert.equal(lib.refusalVerdict(call('{"error":{}}', { status: 502 }), { dryRun: false }), "FAIL");
});

test("Case 6c live: TypeSafe accepting an odd question is a note, a 5xx fails", () => {
  const upstream400 = call('{"error":{"message":"bad"}}', { status: 400, headers: { "x-agentcc-upstream-status": "400" } });
  assert.equal(lib.semanticVerdict([upstream400], { dryRun: false }), "PASS");
  assert.equal(lib.semanticVerdict([upstream400, call(mockText({ type: "choice" }))], { dryRun: false }), "NOTE");
  assert.equal(lib.semanticVerdict([call('{"error":{}}', { status: 502, headers: { "x-agentcc-upstream-status": "500" } })], { dryRun: false }), "FAIL");
});

test("Case 6f: live refusals other than 404 are a note; dry run wants the mock's pass-through 400; 502 fails", () => {
  const err = (status, code, upstream) => call(JSON.stringify({ error: { code } }),
    { status, headers: upstream ? { "x-agentcc-upstream-status": upstream } : {} });
  assert.equal(lib.unknownModelVerdict(err(404, "model_not_found", "404"), { dryRun: false }), "PASS");
  assert.equal(lib.unknownModelVerdict(err(400, "upstream_rejected", "400"), { dryRun: false }), "NOTE");
  assert.equal(lib.unknownModelVerdict(err(502, "upstream_error", "500"), { dryRun: false }), "FAIL");
  const mockRefusal = call(JSON.stringify({ error: { code: "upstream_rejected", message: "invalid synthetic decision request" } }),
    { status: 400, headers: { "x-agentcc-upstream-status": "400" } });
  assert.equal(lib.unknownModelVerdict(mockRefusal, { dryRun: true }), "PASS");
  // A dry run tells a pass-through from a local refusal or a 200.
  assert.equal(lib.unknownModelVerdict(err(400, "upstream_rejected", ""), { dryRun: true }), "FAIL");
  assert.equal(lib.unknownModelVerdict(call(mockText({ type: "noul" })), { dryRun: true }), "FAIL");
  assert.equal(lib.unknownModelVerdict(err(502, "upstream_error", "500"), { dryRun: true }), "FAIL");
});
