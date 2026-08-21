import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { streamHarness } from "../streamHarness";

/** A Response whose body streams the given chunks, like the harness does. */
const streamingResponse = (chunks) => ({
  ok: true,
  status: 200,
  body: {
    getReader() {
      const encoder = new TextEncoder();
      let i = 0;
      return {
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
        releaseLock: () => {},
      };
    },
  },
});

const refusal = (status, body) => ({
  ok: false,
  status,
  json: async () => body,
});

let fetchMock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("streamHarness", () => {
  it("delivers each event in order as it arrives", async () => {
    fetchMock.mockResolvedValue(
      streamingResponse([
        'data: {"kind":"text","text":"reading the agent"}\n\n',
        'data: {"kind":"tool","tool":"save_world"}\n\n',
        'data: {"kind":"done"}\n\n',
      ])
    );
    const seen = [];
    await streamHarness({ path: "/api/say", body: { text: "hi" }, onEvent: (e) => seen.push(e) });
    expect(seen.map((e) => e.kind)).toEqual(["text", "tool", "done"]);
  });

  it("posts the body as JSON to the given path", async () => {
    fetchMock.mockResolvedValue(streamingResponse(['data: {"kind":"done"}\n\n']));
    await streamHarness({ path: "/api/say", body: { text: "build the world" }, onEvent: () => {} });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/say$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ text: "build the world" });
  });

  it("raises the harness's own sentence when it refuses mid-turn", async () => {
    fetchMock.mockResolvedValue(
      refusal(409, { error: "still working on the build stage — one moment" })
    );
    await expect(
      streamHarness({ path: "/api/say", body: { text: "hi" }, onEvent: () => {} })
    ).rejects.toThrow(/still working on the build stage/);
  });

  it("raises a readable error when a refusal carries no body", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });
    await expect(
      streamHarness({ path: "/api/run", body: { text: "" }, onEvent: () => {} })
    ).rejects.toThrow(/500/);
  });

  it("reports the last status event so callers can resync without re-reading", async () => {
    fetchMock.mockResolvedValue(
      streamingResponse([
        'data: {"kind":"text","text":"working"}\n\n',
        'data: {"kind":"status","detail":{"busy":false}}\n\n',
      ])
    );
    const result = await streamHarness({ path: "/api/say", body: {}, onEvent: () => {} });
    expect(result.lastStatus).toEqual({ busy: false });
  });
});
