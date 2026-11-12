// Correlation and telemetry (CP P9, OBS2, OBS3; READINESS L13): one trace id across the browser,
// Django and EE, `server-timing`, one access line per request, and spans to Observe.
import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { loadConfig } from "../src/policy.mjs";
import { createSpanExporter, exportRequest, spanOf } from "../src/otlp.mjs";
import { accessRecord, hashTenant, parseTraceparent, startRequest } from "../src/trace.mjs";
import { claimsOf, cloudEnv, json, mintAssertion, startGateway } from "./helpers.mjs";

const INBOUND = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";

test("traceparent is parsed strictly", () => {
  assert.deepEqual(parseTraceparent(INBOUND), { traceId: "0af7651916cd43dd8448eb211c80319c", parentSpanId: "b7ad6b7169203331", flags: "01" });
  for (const bad of [undefined, "", "01-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01", `00-${"0".repeat(32)}-b7ad6b7169203331-01`,
    `00-0af7651916cd43dd8448eb211c80319c-${"0".repeat(16)}-01`, "00-0AF7651916CD43DD8448EB211C80319C-b7ad6b7169203331-01", "00-abc-def-01"]) {
    assert.equal(parseTraceparent(bad), null, String(bad));
  }
});

test("an inbound trace is continued through the issuer and EE, and its id is the request id", async (t) => {
  const { base, calls } = await startGateway(t, { config: loadConfig(cloudEnv(t)) });
  const response = await fetch(`${base}/workspace/usage`, { headers: { authorization: "Bearer browser", traceparent: INBOUND, tracestate: "fi=1" } });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-request-id"), "0af7651916cd43dd8448eb211c80319c");
  const toIssuer = calls.exchange[0].init.headers;
  const toEe = calls.upstream[0].init.headers;
  for (const headers of [toIssuer, toEe]) {
    const parsed = parseTraceparent(headers.traceparent);
    assert.equal(parsed.traceId, "0af7651916cd43dd8448eb211c80319c");
    assert.notEqual(parsed.parentSpanId, "b7ad6b7169203331", "the gateway's own span is the parent");
    assert.equal(headers["x-request-id"], "0af7651916cd43dd8448eb211c80319c");
    assert.equal(headers.tracestate, "fi=1");
  }
  assert.match(response.headers.get("server-timing"), /^mint;dur=\d+\.\d, gw;dur=\d+\.\d, ee;dur=\d+\.\d$/);
});

test("without a trace a new one starts; every refusal carries its id in the body", async (t) => {
  const { base } = await startGateway(t, { config: loadConfig(cloudEnv(t)) });
  const ids = new Set();
  for (const [path, headers] of [["/os/agents", {}], ["/workspace/usage", {}], ["/workspace/usage", { traceparent: "garbage" }]]) {
    const response = await fetch(`${base}${path}`, { headers });
    const body = await response.json();
    assert.ok(response.status >= 400);
    assert.match(body.requestId, /^[0-9a-f]{32}$/);
    assert.equal(body.requestId, response.headers.get("x-request-id"));
    ids.add(body.requestId);
  }
  assert.equal(ids.size, 3);
});

test("the access line names the route template and hashed tenant, never ids, paths, tokens or bodies", async (t) => {
  const lines = [];
  const assertion = mintAssertion();
  const { base } = await startGateway(t, { config: loadConfig(cloudEnv(t)), log: (record) => lines.push(record) });
  const response = await fetch(`${base}/workspace/sandboxes/sbx-secret-name/files/private%20notes.txt?path=x`, {
    method: "PUT", body: "secret file body", headers: { authorization: `Bearer ${assertion}` },
  });
  assert.equal(response.status, 200);
  await new Promise((resolve) => setTimeout(resolve, 20));
  const line = lines.find((record) => record.event === "omega-gateway.request");
  assert.equal(line.route, "/workspace/sandboxes/{sandbox}/files/{filePath}");
  assert.equal(line.method, "PUT");
  assert.equal(line.status, 200);
  assert.equal(line.rateClass, "write");
  assert.equal(line.org, hashTenant(claimsOf(assertion).org_id));
  assert.equal(line.workspace, hashTenant(claimsOf(assertion).workspace_id));
  const text = JSON.stringify(lines);
  for (const leak of ["sbx-secret-name", "private", "secret file body", "omega_delegate_", claimsOf(assertion).org_id, claimsOf(assertion).sub, "path=x"]) {
    assert.equal(text.includes(leak), false, `access line leaks ${leak}`);
  }
});

test("a refusal's access line carries its reason", async (t) => {
  const lines = [];
  const { base } = await startGateway(t, { config: loadConfig(cloudEnv(t)), log: (record) => lines.push(record) });
  await (await fetch(`${base}/workspace/sandboxes/a%2Fb`, { headers: { authorization: `Bearer ${mintAssertion()}` } })).arrayBuffer();
  await (await fetch(`${base}/workspace/workforce/agent-drafts`, { method: "POST", body: "{}", headers: { authorization: `Bearer ${mintAssertion()}` } })).arrayBuffer();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(lines.map((line) => [line.status, line.reason, line.route]), [[403, "path_refused", "unmatched"], [403, "role_below_floor", "/workspace/workforce/agent-drafts"]]);
});

test("spans go to the collector as OTLP JSON with the Observe project and key pair; a 5xx is an error span", async (t) => {
  const received = [];
  const collector = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      received.push({ headers: request.headers, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
      response.writeHead(200, { "content-type": "application/json" });
      response.end("{}");
    });
  });
  await new Promise((resolve) => collector.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => collector.close(resolve)));
  const exporter = createSpanExporter({
    url: `http://127.0.0.1:${collector.address().port}/v1/traces`, credentials: { apiKey: "pk", secretKey: "sk" },
    projectName: "omega-console-ops", serviceName: "omega-gateway",
  });
  const { base } = await startGateway(t, {
    config: loadConfig(cloudEnv(t)), exporter,
    handler: { upstream: (url) => (url.endsWith("/usage") ? json({ error: "boom" }, 500) : json({ ok: true })) },
  });
  const failed = await fetch(`${base}/workspace/usage`, { headers: { authorization: `Bearer ${mintAssertion()}`, traceparent: INBOUND } });
  assert.equal(failed.status, 500);
  await failed.arrayBuffer();
  await (await fetch(`${base}/workspace/capabilities`, { headers: { authorization: `Bearer ${mintAssertion()}` } })).arrayBuffer();
  await new Promise((resolve) => setTimeout(resolve, 20));
  await exporter.flush();
  assert.equal(received.length, 1);
  assert.equal(received[0].headers["x-api-key"], "pk");
  assert.equal(received[0].headers["x-secret-key"], "sk");
  const resource = received[0].body.resourceSpans[0];
  assert.ok(resource.resource.attributes.some((a) => a.key === "project_name" && a.value.stringValue === "omega-console-ops"));
  const spans = resource.scopeSpans[0].spans;
  assert.equal(spans.length, 2);
  const error = spans.find((span) => span.name === "GET /workspace/usage");
  assert.equal(error.traceId, "0af7651916cd43dd8448eb211c80319c");
  assert.equal(error.parentSpanId, "b7ad6b7169203331");
  assert.equal(error.status.code, 2);
  assert.equal(error.traceId, failed.headers.get("x-request-id"));
  assert.equal(spans.find((span) => span.name === "GET /workspace/capabilities").status.code, 0);
  assert.equal(exporter.stats.exported, 2);
});

test("export never blocks a request: a dead collector drops spans and counts them; the queue is bounded", async (t) => {
  const exporter = createSpanExporter({ url: "http://127.0.0.1:9/v1/traces", credentials: null, projectName: "p", serviceName: "s" });
  t.after(() => exporter.shutdown());
  const fake = { headers: {}, method: "GET" };
  for (let i = 0; i < 5_000; i++) {
    const context = startRequest(fake);
    context.status = 200;
    exporter.record(context);
  }
  // One batch of 256 is already in flight; the rest of the buffer holds at most 4,096.
  assert.ok(exporter.stats.dropped >= 5_000 - 4_096 - 256, `dropped ${exporter.stats.dropped}`);
  await exporter.flush();
  assert.equal(exporter.stats.exported, 0);
  assert.equal(exporter.stats.dropped, 5_000);
});

test("a span carries no raw id; its request shape matches the OTLP JSON encoding", () => {
  const context = startRequest({ headers: {}, method: "POST" });
  context.route = "/workspace/conversations/{id}/messages";
  context.status = 202;
  context.tenant = { org: "org-raw-id", workspace: "ws-raw-id" };
  const span = spanOf(context);
  assert.equal(JSON.stringify(span).includes("raw-id"), false);
  assert.equal(span.kind, 2);
  assert.match(span.startTimeUnixNano, /^\d+$/);
  const body = exportRequest([span], { projectName: "omega-console-ops", serviceName: "omega-gateway" });
  assert.equal(body.resourceSpans[0].scopeSpans[0].spans[0], span);
  assert.equal(accessRecord(context).workspace, hashTenant("ws-raw-id"));
});
