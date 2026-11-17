import { describe, expect, it, vi } from "vitest";

import { createSseParser, openOmegaStream } from "../omegaStream";
import { OMEGA_STATE } from "../omegaStates";

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

describe("createSseParser", () => {
  it("parses ids, event types, multi-line data, comments and CRLF across chunk boundaries", () => {
    const events = [];
    const parser = createSseParser((event) => events.push(event));
    parser.push(": keep-alive\r\nid: 7\r\nevent: stdout\r\nda");
    parser.push('ta: {"text":"a"}\r\n\r\ndata: line one\ndata: line two\n\n');
    parser.push("event: empty\n\n");
    expect(events).toEqual([
      { id: "7", event: "stdout", data: '{"text":"a"}', json: { text: "a" } },
      {
        id: "7",
        event: "message",
        data: "line one\nline two",
        json: undefined,
      },
    ]);
    expect(parser.lastEventId).toBe("7");
  });
});

describe("openOmegaStream", () => {
  it("resumes a dropped GET stream with Last-Event-ID and a fresh request", async () => {
    const omegaFetch = vi
      .fn()
      .mockResolvedValueOnce(
        ok(streamOf(['id: 1\ndata: {"n":1}\n\n'], { failAfter: true })),
      )
      .mockResolvedValueOnce(ok(streamOf(['id: 2\ndata: {"n":2}\n\n'])));
    const seen = [];
    const { done } = openOmegaStream("/workspace/deployments/dep-1/logs", {
      omegaFetch,
      retryDelayMs: 1,
      onEvent: (e) => seen.push(e.json.n),
    });
    await expect(done).resolves.toEqual({ ended: true });
    expect(seen).toEqual([1, 2]);
    expect(omegaFetch).toHaveBeenCalledTimes(2);
    expect(omegaFetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      raw: true,
      headers: {},
    });
    expect(omegaFetch.mock.calls[1][1].headers).toEqual({
      "Last-Event-ID": "1",
    });
  });

  it("never re-sends a POST stream such as sandbox exec", async () => {
    const omegaFetch = vi
      .fn()
      .mockResolvedValueOnce(
        ok(streamOf(["event: stdout\ndata: hi\n\n"], { failAfter: true })),
      );
    const onState = vi.fn();
    const { done } = openOmegaStream("/workspace/sandboxes/sbx-1/exec", {
      method: "POST",
      json: { command: ["ls"] },
      omegaFetch,
      onState,
    });
    await expect(done).resolves.toMatchObject({
      ok: false,
      state: OMEGA_STATE.UNAVAILABLE,
      code: "stream_interrupted",
    });
    expect(omegaFetch).toHaveBeenCalledTimes(1);
    expect(omegaFetch.mock.calls[0][1]).toMatchObject({
      method: "POST",
      json: { command: ["ls"] },
    });
    expect(onState).toHaveBeenCalledWith(
      expect.objectContaining({ code: "stream_interrupted" }),
    );
  });

  it("stops on a refusal and retries only while the gateway is unavailable", async () => {
    const refused = {
      ok: false,
      state: OMEGA_STATE.REFUSED,
      status: 403,
      code: "route_not_allowed",
    };
    const omegaFetch = vi.fn().mockResolvedValue(refused);
    const { done } = openOmegaStream("/workspace/sandboxes/sbx-1/events", {
      omegaFetch,
      retryDelayMs: 1,
    });
    await expect(done).resolves.toBe(refused);
    expect(omegaFetch).toHaveBeenCalledTimes(1);

    const down = {
      ok: false,
      state: OMEGA_STATE.UNAVAILABLE,
      status: 502,
      code: "omega_remote_unreachable",
    };
    const flaky = vi
      .fn()
      .mockResolvedValueOnce(down)
      .mockResolvedValueOnce(ok(streamOf(["data: x\n\n"])));
    await expect(
      openOmegaStream("/workspace/deployments/dep-1/logs", {
        omegaFetch: flaky,
        retryDelayMs: 1,
      }).done,
    ).resolves.toEqual({ ended: true });
    expect(flaky).toHaveBeenCalledTimes(2);
  });

  it("close() ends the stream without reconnecting", async () => {
    let cancel;
    const omegaFetch = vi.fn(async (path, init) =>
      ok(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode("data: first\n\n"));
            init.signal.addEventListener("abort", () =>
              controller.error(new DOMException("aborted", "AbortError")),
            );
          },
          cancel() {
            cancel = true;
          },
        }),
      ),
    );
    const seen = [];
    const stream = openOmegaStream("/workspace/deployments/dep-1/logs", {
      omegaFetch,
      onEvent: (e) => {
        seen.push(e.data);
        stream.close();
      },
    });
    await expect(stream.done).resolves.toEqual({ closed: true });
    expect(seen).toEqual(["first"]);
    expect(omegaFetch).toHaveBeenCalledTimes(1);
    expect(cancel).toBeUndefined();
  });

  it("without terminalEvents a clean end is the end, as before (the Deployments log viewer relies on it)", async () => {
    const omegaFetch = vi
      .fn()
      .mockResolvedValueOnce(
        ok(streamOf(['id: 4\nevent: log\ndata: {"seq":4}\n\n'])),
      );
    await expect(
      openOmegaStream("/workspace/deployments/dep-1/logs", { omegaFetch }).done,
    ).resolves.toEqual({ ended: true });
    expect(omegaFetch).toHaveBeenCalledTimes(1);
  });
});
