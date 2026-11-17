// Browser harness for the shared Omega client (READINESS lane 1.5). It runs the real
// modules (omegaClient, omegaStream, sessionAssertion with its default minter) in
// Chromium, WebKit and Firefox against harness-server.mjs. run-browser-checks.mjs drives it.
import { createOmegaClient } from "../../omegaClient";
import { openOmegaStream } from "../../omegaStream";

const out = () => document.getElementById("out");

function show(code, result) {
  const section = document.createElement("section");
  section.dataset.code = code;
  const title = document.createElement("h3");
  title.textContent = result.ok ? "ok" : result.title || "";
  const body = document.createElement("p");
  body.textContent = result.ok ? "" : result.message || "";
  section.append(title, body);
  out().append(section);
}

async function copy(cases, base = "/omega/api") {
  const client = createOmegaClient({ basePath: base });
  const results = [];
  for (const { status, code, exchange, detail, message } of cases) {
    await fetch("/control/exchange", {
      method: "POST",
      body: JSON.stringify({ mode: exchange || "ok" }),
    });
    const result = await client.omegaFetch(`/refuse/${status}/${code}`, {
      query: detail ? { detail: JSON.stringify(detail), message } : undefined,
    });
    const key = detail ? `${code}:${detail.reason}:${detail.verb}` : code;
    show(exchange ? `exchange:${exchange}` : key, result);
    results.push({
      code,
      key,
      exchange: exchange || null,
      ok: result.ok,
      state: result.state,
      status: result.status,
      resultCode: result.code,
      title: result.title,
      message: result.message,
    });
  }
  await fetch("/control/exchange", {
    method: "POST",
    body: JSON.stringify({ mode: "ok" }),
  });
  return results;
}

function collect(path, options, base = "/omega/api") {
  const client = createOmegaClient({ basePath: base });
  const started = performance.now();
  const events = [];
  const opens = [];
  const stream = openOmegaStream(path, {
    ...options,
    omegaFetch: client.omegaFetch,
    onOpen: (open) =>
      opens.push({ ...open, at: Math.round(performance.now() - started) }),
    onEvent: (event) =>
      events.push({
        id: event.id,
        event: event.event,
        json: event.json ?? null,
        at: Math.round(performance.now() - started),
      }),
  });
  return stream.done.then((result) => ({
    result,
    events,
    opens,
    elapsedMs: Math.round(performance.now() - started),
  }));
}

const reattach = ({ firstEvent }) =>
  firstEvent?.json?.execId
    ? `/workspace/sandboxes/s1/exec/${encodeURIComponent(firstEvent.json.execId)}/events`
    : null;

window.omegaHarness = {
  copy,
  feed: (options = {}) =>
    collect("/workspace/feed", {
      terminalEvents: ["end"],
      retryDelayMs: 50,
      ...options,
    }),
  buffered: () =>
    collect(
      "/workspace/feed",
      { terminalEvents: ["end"], transport: "auto" },
      "/buffered/omega/api",
    ),
  exec: (mode, seconds) =>
    collect("/workspace/sandboxes/s1/exec", {
      method: "POST",
      json: { command: `sleep ${seconds}; echo done`, mode, seconds },
      terminalEvents: ["exit", "error"],
      reattach,
      retryDelayMs: 200,
    }),
};
window.omegaHarnessReady = true;
