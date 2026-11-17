import { afterEach, describe, expect, it, vi } from "vitest";

import { createOmegaClient } from "../omegaClient";
import { openOmegaStream } from "../omegaStream";
import { OMEGA_STATE } from "../omegaStates";
import { currentStreamMode, resetStreamMode } from "../streamMode";

const encoder = new TextEncoder();

// Deliver the chunks first, then end or fail on the next pull: erroring a
// stream in start() would discard chunks that were already queued.
function streamOf(chunks, { failAfter = false } = {}) {
  let sent = false;
  return new ReadableStream({
    pull(controller) {
      if (!sent) {
        sent = true;
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        return;
      }
      if (failAfter) controller.error(new TypeError("network changed"));
      else controller.close();
    },
  });
}

const ok = (body) => ({ ok: true, status: 200, response: { body } });

describe("openOmegaStream: cuts without a terminal event (L06, unit)", () => {
  afterEach(() => resetStreamMode());

  it("comes straight back after a clean cut, with Last-Event-ID, until the terminal event", async () => {
    const omegaFetch = vi
      .fn()
      .mockResolvedValueOnce(
        ok(streamOf(['id: 1\nevent: log\ndata: {"n":1}\n\n'])),
      )
      .mockResolvedValueOnce(
        ok(streamOf(['id: 2\nevent: log\ndata: {"n":2}\n\n'])),
      )
      .mockResolvedValueOnce(
        ok(
          streamOf([
            'id: 3\nevent: log\ndata: {"n":3}\n\nevent: end\ndata: {}\n\n',
          ]),
        ),
      );
    const seen = [];
    const opens = [];
    const { done } = openOmegaStream("/workspace/deployments/dep-1/logs", {
      omegaFetch,
      terminalEvents: ["end"],
      onEvent: (e) => e.json?.n && seen.push(e.json.n),
      onOpen: (o) => opens.push(o.attempt),
    });
    await expect(done).resolves.toEqual({ ended: true });
    expect(seen).toEqual([1, 2, 3]);
    expect(opens).toEqual([1, 2, 3]);
    expect(omegaFetch.mock.calls.map((c) => c[1].headers)).toEqual([
      {},
      { "Last-Event-ID": "1" },
      { "Last-Event-ID": "2" },
    ]);
  });

  it("ends a non-resumable stream cut before its terminal event with stream_cut, never re-sending it", async () => {
    const omegaFetch = vi
      .fn()
      .mockResolvedValueOnce(
        ok(streamOf(['id: 1\nevent: started\ndata: {"execId":"x1"}\n\n'])),
      );
    const onState = vi.fn();
    const result = await openOmegaStream("/workspace/sandboxes/s1/exec", {
      method: "POST",
      json: { command: "sleep 90" },
      terminalEvents: ["exit", "error"],
      omegaFetch,
      onState,
    }).done;
    expect(result).toMatchObject({
      ok: false,
      state: OMEGA_STATE.UNAVAILABLE,
      code: "stream_cut",
      title: "The live view stopped",
    });
    expect(omegaFetch).toHaveBeenCalledTimes(1);
    expect(onState).toHaveBeenCalledWith(
      expect.objectContaining({ code: "stream_cut" }),
    );
  });

  it("reattaches a cut exec to its events route and reads it to the exit", async () => {
    const omegaFetch = vi
      .fn()
      .mockResolvedValueOnce(
        ok(
          streamOf(
            [
              'id: 1\nevent: started\ndata: {"execId":"x1"}\n\nid: 2\nevent: stdout\ndata: {"data":"a"}\n\n',
            ],
            { failAfter: true },
          ),
        ),
      )
      .mockResolvedValueOnce(
        ok(streamOf(['id: 3\nevent: exit\ndata: {"exitCode":0}\n\n'])),
      );
    const reattach = vi.fn(({ firstEvent }) =>
      firstEvent?.json?.execId
        ? `/workspace/sandboxes/s1/exec/${firstEvent.json.execId}/events`
        : null,
    );
    const events = [];
    const result = await openOmegaStream("/workspace/sandboxes/s1/exec", {
      method: "POST",
      json: { command: "sleep 90" },
      terminalEvents: ["exit", "error"],
      reattach,
      retryDelayMs: 1,
      omegaFetch,
      onEvent: (e) => events.push(e.event),
    }).done;
    expect(result).toEqual({ ended: true });
    expect(events).toEqual(["started", "stdout", "exit"]);
    expect(reattach).toHaveBeenCalledWith(
      expect.objectContaining({
        lastEventId: "2",
        firstEvent: expect.objectContaining({ event: "started" }),
      }),
    );
    expect(omegaFetch.mock.calls[1][0]).toBe(
      "/workspace/sandboxes/s1/exec/x1/events",
    );
    expect(omegaFetch.mock.calls[1][1]).toMatchObject({
      method: "GET",
      json: undefined,
      headers: { "Last-Event-ID": "2" },
    });
  });

  it("a stream that dropped after its terminal event is finished, not lost", async () => {
    const omegaFetch = vi.fn().mockResolvedValueOnce(
      ok(
        streamOf(['event: exit\ndata: {"exitCode":0}\n\n'], {
          failAfter: true,
        }),
      ),
    );
    await expect(
      openOmegaStream("/workspace/sandboxes/s1/exec", {
        method: "POST",
        terminalEvents: ["exit"],
        omegaFetch,
      }).done,
    ).resolves.toEqual({ ended: true });
  });

  it("gives up on a route that keeps closing at once with nothing in it", async () => {
    const omegaFetch = vi.fn(async () => ok(streamOf([])));
    const onState = vi.fn();
    const result = await openOmegaStream("/workspace/deployments/dep-1/logs", {
      omegaFetch,
      terminalEvents: ["end"],
      retryDelayMs: 1,
      maxEmptyCuts: 3,
      onState,
    }).done;
    expect(result).toMatchObject({ ok: false, code: "stream_cut" });
    expect(omegaFetch).toHaveBeenCalledTimes(3);
    expect(onState).toHaveBeenCalledTimes(1);
  });
});

describe("openOmegaStream: a buffering proxy (CH-12, unit)", () => {
  afterEach(() => resetStreamMode());

  // A response whose body never yields a byte until aborted: what a proxy that
  // holds the whole response looks like from the browser.
  const held = (init) =>
    ok(
      new ReadableStream({
        start(controller) {
          init.signal.addEventListener("abort", () =>
            controller.error(new DOMException("aborted", "AbortError")),
          );
        },
      }),
    );

  it("switches to long-poll windows when no byte arrives in time, and resumes where it was", async () => {
    const calls = [];
    const omegaFetch = vi.fn(async (path, init) => {
      calls.push({ query: init.query, headers: init.headers });
      if (init.query.transport === "stream") return held(init);
      if (calls.length === 2)
        return ok(streamOf(['id: 1\nevent: log\ndata: {"n":1}\n\n']));
      return ok(
        streamOf([
          'id: 2\nevent: log\ndata: {"n":2}\n\nevent: end\ndata: {}\n\n',
        ]),
      );
    });
    const started = Date.now();
    const result = await openOmegaStream("/workspace/conversations/c1/events", {
      omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
      bufferingTimeoutMs: 60,
      pollWaitMs: 5000,
      query: { source: "build" },
    }).done;
    expect(result).toEqual({ ended: true, mode: "poll" });
    expect(Date.now() - started).toBeLessThan(2000);
    expect(calls.map((c) => c.query)).toEqual([
      { source: "build", transport: "stream" },
      { source: "build", transport: "poll", wait: 5000 },
      { source: "build", transport: "poll", wait: 5000 },
    ]);
    expect(calls[2].headers).toEqual({ "Last-Event-ID": "1" });
    expect(currentStreamMode()).toBe("poll");

    // The next stream in this document polls at once.
    const next = vi.fn(async () => ok(streamOf(["event: end\ndata: {}\n\n"])));
    await openOmegaStream("/workspace/conversations/c2/events", {
      omegaFetch: next,
      transport: "auto",
      terminalEvents: ["end"],
    }).done;
    expect(next.mock.calls[0][1].query).toEqual({
      transport: "poll",
      wait: 25000,
    });
  });

  it("keeps streaming when the first byte is in time", async () => {
    const omegaFetch = vi.fn(async () =>
      ok(streamOf([": open\n\nevent: end\ndata: {}\n\n"])),
    );
    const result = await openOmegaStream("/workspace/conversations/c1/events", {
      omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
      bufferingTimeoutMs: 5000,
    }).done;
    expect(result).toEqual({ ended: true, mode: "stream" });
    expect(currentStreamMode()).toBe("stream");
  });

  it("times the first byte from when the request goes out, not from before a slow mint (regression)", async () => {
    // Verifier finding 4 on 601bf7b1a: the 8 s timer started before omegaFetch
    // minted its key, so a 9 s session exchange read as a buffering proxy.
    // Scaled: a 100 ms timer and a 300 ms mint against a route that streams.
    const sentAt = [];
    let minted = 0;
    const started = Date.now();
    const mintAssertion = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      minted += 1;
      return { token: `omega_delegate_a.b.${minted}`, expiresAt: 0 };
    });
    const fetchImpl = vi.fn(async (url, init) => {
      if (init.signal?.aborted) throw new DOMException("aborted", "AbortError");
      sentAt.push(Date.now() - started);
      expect(init).not.toHaveProperty("onSend");
      return new Response(streamOf([": open\n\nevent: end\ndata: {}\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });
    const client = createOmegaClient({
      basePath: "/omega/api",
      fetchImpl,
      mintAssertion,
    });
    const result = await openOmegaStream("/workspace/conversations/c1/events", {
      omegaFetch: client.omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
      bufferingTimeoutMs: 100,
    }).done;
    expect(result).toEqual({ ended: true, mode: "stream" });
    expect(currentStreamMode()).toBe("stream");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(sentAt[0]).toBeGreaterThanOrEqual(290);
  });

  it("still detects a buffering proxy behind a slow mint, timed from the send", async () => {
    const mintAssertion = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
      return { token: "omega_delegate_a.b.c", expiresAt: 0 };
    });
    let heldForMs = null;
    const fetchImpl = vi.fn(async (url, init) => {
      if (init.signal?.aborted) throw new DOMException("aborted", "AbortError");
      if (url.includes("transport=stream")) {
        const sentAt = Date.now();
        // A proxy that holds even the headers until the connection is cut.
        return new Promise((resolve, reject) =>
          init.signal.addEventListener("abort", () => {
            heldForMs = Date.now() - sentAt;
            reject(new DOMException("aborted", "AbortError"));
          }),
        );
      }
      return new Response(streamOf(["event: end\ndata: {}\n\n"]), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });
    const client = createOmegaClient({
      basePath: "/omega/api",
      fetchImpl,
      mintAssertion,
    });
    const result = await openOmegaStream("/workspace/conversations/c1/events", {
      omegaFetch: client.omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
      bufferingTimeoutMs: 100,
    }).done;
    expect(result).toEqual({ ended: true, mode: "poll" });
    // The held request went out after its 200 ms mint and was cut about
    // 100 ms later; before the fix it was cut during the mint, never sent.
    expect(heldForMs).toBeGreaterThanOrEqual(90);
    expect(heldForMs).toBeLessThan(1000);
    expect(currentStreamMode()).toBe("poll");
  });

  it("refuses auto transport where it cannot be honoured", () => {
    expect(() =>
      openOmegaStream("/workspace/sandboxes/s1/exec", {
        method: "POST",
        transport: "auto",
        terminalEvents: ["exit"],
      }),
    ).toThrow(TypeError);
    expect(() =>
      openOmegaStream("/workspace/deployments/d/logs", { transport: "auto" }),
    ).toThrow(TypeError);
  });

  it("does not count short empty long-poll windows that ran to their deadline as a broken route", async () => {
    // Behind a buffering proxy with a quiet stream: windows of 1.2 s end empty.
    let windows = 0;
    const omegaFetch = vi.fn(async (path, init) => {
      if (init.query.transport === "stream") {
        return ok(
          new ReadableStream({
            start(controller) {
              init.signal.addEventListener("abort", () =>
                controller.error(new DOMException("aborted", "AbortError")),
              );
            },
          }),
        );
      }
      windows += 1;
      if (windows <= 6) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        return ok(streamOf([]));
      }
      return ok(streamOf(["event: end\ndata: {}\n\n"]));
    });
    const result = await openOmegaStream("/workspace/conversations/c1/events", {
      omegaFetch,
      transport: "auto",
      terminalEvents: ["end"],
      bufferingTimeoutMs: 20,
      pollWaitMs: 1200,
      maxEmptyCuts: 3,
      retryDelayMs: 1,
    }).done;
    expect(result).toEqual({ ended: true, mode: "poll" });
    expect(windows).toBe(7);
  }, 20_000);
});
