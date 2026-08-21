import appAxios from "src/utils/axios";
import { alkBaseUrl, AUTH_HEADERS, isDirectToHarness } from "./client";
import { createSseParser } from "./parseSse";

/**
 * POST to a harness endpoint that answers with server-sent events.
 *
 * axios is not used here on purpose: it buffers the whole response, which defeats the point
 * of a stream. EventSource cannot be used either — it is GET only, and both of these
 * endpoints are POST. So this is fetch plus a manual reader.
 *
 * Resolves once the stream closes, handing back the last status event: every stream ends
 * with one, and it is what the header and tabs resync from.
 */
export const streamHarness = async ({ path, body, onEvent, signal }) => {
  const base = alkBaseUrl(import.meta.env);
  // fetch does not go through the axios instance, so the same headers are applied by hand.
  const headers = { "Content-Type": "application/json" };
  if (!isDirectToHarness(base)) {
    AUTH_HEADERS.forEach((header) => {
      const value = appAxios.defaults.headers.common?.[header];
      if (value) headers[header] = value;
    });
  }

  const response = await fetch(`${base}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
    signal,
  });

  if (!response.ok) {
    // A refusal carries the reason in its body — 409 while a stage is running, 400 when
    // there is nothing to run. That sentence is more useful than any wording of ours.
    let reason = "";
    try {
      reason = (await response.json())?.error || "";
    } catch {
      reason = "";
    }
    throw new Error(reason || `The harness answered ${response.status}.`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let lastStatus = null;

  const parser = createSseParser((event) => {
    if (event.kind === "status") lastStatus = event.detail ?? null;
    onEvent(event);
  });

  try {
    for (;;) {
      // eslint-disable-next-line no-await-in-loop
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
  } finally {
    parser.end();
    reader.releaseLock();
  }

  return { lastStatus };
};
