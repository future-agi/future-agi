import axios, { endpoints } from "src/utils/axios";

import { readManagedRefusal } from "../utils/managedRefusals";

// One request through the gateway's test endpoint: Django creates a temporary
// key, sends one prompt through this gateway (a real request, billed and
// logged like any other), revokes the key and returns the gateway's status,
// headers and body. The model page's Try it and the Guardrails Test tab both
// send it; each reads the answer its own way. Try it can also stream it
// (test-playground-stream/): the same request, answered as server-sent
// events while the model writes (agentcc/services/playground_stream.py).

// A model's first answer can take longer than the app-wide request timeout.
const TEST_TIMEOUT_MS = 90000;

// A deployment without delegated calls refuses the test with this 409 code
// before any gateway call (agentcc/services/delegated_availability.py). The
// gateway read says so up front as delegated_playground_available: false.
export const TESTS_UNAVAILABLE_CODE = "feature_unavailable_733";

/**
 * Whether this release runs no test requests: the gateway read says so, or a
 * test was refused with TESTS_UNAVAILABLE_CODE (`facts` from
 * requestFailureFacts). An older server that sends no flag stays available.
 */
export function testsUnavailable(available, facts) {
  return available === false || facts?.code === TESTS_UNAVAILABLE_CODE;
}

// The sampling parameters the test endpoint takes; each is sent only when set.
export const TEST_PARAMS = ["temperature", "max_tokens", "top_p"];

export function gatewayTestBody({ prompt, model, systemPrompt, params }) {
  const body = { prompt: (prompt || "").trim() };
  if (model && model.trim()) body.model = model.trim();
  if (systemPrompt && systemPrompt.trim()) {
    body.system_prompt = systemPrompt.trim();
  }
  TEST_PARAMS.forEach((name) => {
    const raw = params?.[name];
    if (raw === "" || raw == null) return;
    const value = Number(raw);
    if (Number.isFinite(value)) body[name] = value;
  });
  return body;
}

// Preserve the original envelope, including metadata and headers, for both
// consumers. Classifying a refusal never schedules or sends another request.
function withManagedRefusal(result) {
  const refusal = readManagedRefusal(result);
  return refusal ? { ...result, refusal } : result;
}

export async function sendGatewayTest(gatewayId, body) {
  const { data } = await axios.post(
    endpoints.gateway.testPlayground(gatewayId),
    body,
    { timeout: TEST_TIMEOUT_MS },
  );
  return withManagedRefusal(data?.result || data);
}

/**
 * The complete server-sent events in `text`, as [{ event, data }] with data
 * parsed. The part after the last blank line is still arriving and is left
 * for the next read.
 */
export function parseEvents(text) {
  const blocks = String(text || "").split("\n\n");
  blocks.pop();
  const events = [];
  blocks.forEach((block) => {
    let event = "message";
    let data = "";
    block.split("\n").forEach((line) => {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    });
    if (!data) return;
    try {
      const parsed = JSON.parse(data);
      events.push({
        event,
        data: event === "result" ? withManagedRefusal(parsed) : parsed,
      });
    } catch {
      // Not ours to read; the stream goes on.
    }
  });
  return events;
}

/**
 * The streamed test. `onEvents(events)` gets every event read so far each
 * time more arrives; the promise resolves with all of them. `signal` stops
 * the request (the server then revokes its key).
 */
export async function streamGatewayTest(gatewayId, body, { onEvents, signal }) {
  const { data } = await axios.post(
    endpoints.gateway.testPlaygroundStream(gatewayId),
    body,
    {
      timeout: TEST_TIMEOUT_MS,
      signal,
      // The browser's request keeps what has arrived in responseText.
      onDownloadProgress: (progress) => {
        const text = progress?.event?.target?.responseText;
        if (typeof text === "string") onEvents?.(parseEvents(text));
      },
    },
  );
  return parseEvents(typeof data === "string" ? data : "");
}
