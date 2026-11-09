// CH-12's buffering watchdog against the real client (unit, regression).
// Verifier finding 4 on 1bf593b37: a refusal that omegaFetch retries after a
// pause (a 503 that asked to wait, a stale key's re-mint) left the watchdog
// running, so it fired during the pause and switched the document to
// long-polling although a whole answer had arrived. Scaled: a 100 ms timer.
import { afterEach, describe, expect, it, vi } from "vitest";

import { createOmegaClient } from "../omegaClient";
import { openOmegaStream } from "../omegaStream";
import { currentStreamMode, resetStreamMode } from "../streamMode";

const encoder = new TextEncoder();
const later = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const aborted = () => new DOMException("aborted", "AbortError");

const sse = (text) =>
  new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(text));
        controller.close();
      },
    }),
    { status: 200, headers: { "content-type": "text/event-stream" } },
  );
const refusal = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });

function streamThrough(answers, { mintMs = () => 0, sleepMs = 150 } = {}) {
  let minted = 0;
  const queue = [...answers];
  const fetchImpl = vi.fn(async (url, init) => {
    if (init.signal?.aborted) throw aborted();
    return queue.shift()(url, init);
  });
  const client = createOmegaClient({
    basePath: "/omega/api",
    fetchImpl,
    mintAssertion: async () => {
      minted += 1;
      await later(mintMs(minted));
      return { token: `omega_delegate_a.b.${minted}`, expiresAt: 0 };
    },
    // Retry-After: 1 scaled to sleepMs; an abort ends the pause, as in pause().
    sleep: (ms, signal) =>
      new Promise((resolve) => {
        const timer = setTimeout(resolve, sleepMs);
        signal?.addEventListener("abort", () => {
          clearTimeout(timer);
          resolve();
        });
      }),
  });
  const stream = openOmegaStream("/workspace/conversations/c1/events", {
    omegaFetch: client.omegaFetch,
    transport: "auto",
    terminalEvents: ["end"],
    bufferingTimeoutMs: 100,
  });
  return { done: stream.done, fetchImpl };
}

const END = ": open\n\nevent: end\ndata: {}\n\n";

describe("the buffering watchdog stops at a refusal (CH-12, regression)", () => {
  afterEach(() => resetStreamMode());

  it("a 503 that asked to wait, answered late in the window, does not read as buffering", async () => {
    const { done, fetchImpl } = streamThrough([
      async () => {
        await later(60);
        return refusal(
          503,
          { error: "verification_unavailable", retryable: true },
          { "retry-after": "1" },
        );
      },
      async () => sse(END),
    ]);
    // Before the fix the 100 ms timer fired during the 150 ms pause:
    // { ended: true, mode: "poll" }, and every later stream polled.
    expect(await done).toEqual({ ended: true, mode: "stream" });
    expect(currentStreamMode()).toBe("stream");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("a stale key's slow re-mint does not read as buffering", async () => {
    const { done, fetchImpl } = streamThrough(
      [
        async () => {
          await later(60);
          return refusal(401, {
            error: "assertion_expired",
            message: "x",
            retryable: true,
          });
        },
        async () => sse(END),
      ],
      { mintMs: (n) => (n === 2 ? 200 : 0) },
    );
    expect(await done).toEqual({ ended: true, mode: "stream" });
    expect(currentStreamMode()).toBe("stream");
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("an OK answer whose body is held is still buffering", async () => {
    const { done } = streamThrough([
      async (url, init) =>
        new Response(
          new ReadableStream({
            start(controller) {
              init.signal.addEventListener("abort", () =>
                controller.error(aborted()),
              );
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        ),
      async () => sse("event: end\ndata: {}\n\n"),
    ]);
    expect(await done).toEqual({ ended: true, mode: "poll" });
    expect(currentStreamMode()).toBe("poll");
  });
});
