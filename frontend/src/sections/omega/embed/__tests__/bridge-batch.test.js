// The parent side of CP P7 (batched one-use keys) and of the telemetry and
// badge additions: one mint per hello, the keys back to back with `left`
// counting down, nothing on a failed mint, and child reports only after a
// hello from the right window and origin.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEmbedBridge } from "../bridge";

const CHILD = "http://127.0.0.1:43211";
const OTHER = "http://127.0.0.1:43212";
const HELLO = { type: "embed.hello", protocol: "omega-embed/1" };
const ERROR = {
  type: "embed.telemetry",
  kind: "error",
  name: "load",
  routeTemplate: "/agents/:agentId",
  release: "r9-2803ad68",
  code: "http_502",
  requestId: "req_6f1c2b9e0a7d4c3b8e5f",
};

const send = (data, { origin = CHILD, source } = {}) =>
  window.dispatchEvent(new MessageEvent("message", { data, origin, source }));
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("omega-embed/1 bridge: batched keys (CP P7)", () => {
  let frame;
  let posted;
  let events;
  let bridge;

  const start = (options) => {
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      onEvent: (event) => events.push(event),
      ...options,
    });
    return bridge;
  };

  beforeEach(() => {
    frame = document.createElement("iframe");
    document.body.appendChild(frame);
    posted = [];
    events = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
      (message, targetOrigin) => posted.push({ message, targetOrigin }),
    );
  });

  afterEach(() => {
    bridge?.dispose();
    frame.remove();
    vi.restoreAllMocks();
  });

  it("answers want=4 with one mint and four keys, left counting down", async () => {
    const getTokens = vi.fn(async (want) =>
      Array.from({ length: want }, (_, i) => `k${i}`),
    );
    start({ getTokens });
    send({ ...HELLO, want: 4 }, { source: frame.contentWindow });
    await flush();

    expect(getTokens).toHaveBeenCalledTimes(1);
    expect(getTokens).toHaveBeenCalledWith(4);
    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "k0", left: 3 },
        targetOrigin: CHILD,
      },
      {
        message: { type: "embed.key", token: "k1", left: 2 },
        targetOrigin: CHILD,
      },
      {
        message: { type: "embed.key", token: "k2", left: 1 },
        targetOrigin: CHILD,
      },
      { message: { type: "embed.key", token: "k3" }, targetOrigin: CHILD },
    ]);
    expect(events.at(-1)).toEqual({ kind: "key-sent", count: 4, want: 4 });
  });

  it("asks for one key for a bare hello, and never more than want", async () => {
    const getTokens = vi.fn(async () => ["a", "b", "c"]);
    start({ getTokens });
    send(HELLO, { source: frame.contentWindow });
    await flush();
    expect(getTokens).toHaveBeenCalledWith(1);
    expect(posted.map((p) => p.message)).toEqual([
      { type: "embed.key", token: "a" },
    ]);
  });

  it("sends what the backend gave when it gave fewer (a backend without batches)", async () => {
    start({ getTokens: async () => ["only"] });
    send({ ...HELLO, want: 6 }, { source: frame.contentWindow });
    await flush();
    expect(posted.map((p) => p.message)).toEqual([
      { type: "embed.key", token: "only" },
    ]);
  });

  it("sends nothing when the mint fails, throws or returns junk", async () => {
    for (const getTokens of [
      async () => {
        throw new Error("503");
      },
      async () => [],
      async () => null,
      async () => ["", 7, null],
    ]) {
      posted.length = 0;
      events.length = 0;
      bridge?.dispose();
      start({ getTokens });
      send({ ...HELLO, want: 4 }, { source: frame.contentWindow });
      await flush();
      expect(posted).toEqual([]);
      expect(events.map((e) => e.kind)).toEqual(["hello", "no-key"]);
    }
  });

  it("drops a hello that arrives while a mint is in flight (one answer per hello)", async () => {
    let release;
    const getTokens = vi.fn(
      () =>
        new Promise((resolve) => {
          release = () => resolve(["x0", "x1"]);
        }),
    );
    start({ getTokens });
    send({ ...HELLO, want: 2 }, { source: frame.contentWindow });
    send({ ...HELLO, want: 2 }, { source: frame.contentWindow });
    release();
    await flush();
    expect(getTokens).toHaveBeenCalledTimes(1);
    expect(posted).toHaveLength(2);
  });

  it("keeps no key: the minted array is emptied after posting", async () => {
    const minted = ["s0", "s1", "s2"];
    start({ getTokens: async () => minted });
    send({ ...HELLO, want: 3 }, { source: frame.contentWindow });
    await flush();
    expect(posted).toHaveLength(3);
    // The bridge posted copies from its own filtered list; nothing it kept
    // still names a key.
    expect(JSON.stringify(events)).not.toMatch(/s[0-2]/);
  });

  it("falls back to getToken when no batch minter is given (Studio)", async () => {
    const getToken = vi.fn(() => "studio_key");
    start({ getToken });
    send({ ...HELLO, want: 4 }, { source: frame.contentWindow });
    await flush();
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(posted.map((p) => p.message)).toEqual([
      { type: "embed.key", token: "studio_key" },
    ]);
  });
});

describe("omega-embed/1 bridge: telemetry and badge", () => {
  let frame;
  let other;
  let events;
  let bridge;

  beforeEach(() => {
    frame = document.createElement("iframe");
    other = document.createElement("iframe");
    document.body.append(frame, other);
    events = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(() => {});
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getTokens: async () => ["k"],
      onEvent: (event) => events.push(event),
    });
  });

  afterEach(() => {
    bridge.dispose();
    frame.remove();
    other.remove();
    vi.restoreAllMocks();
  });

  it("ignores reports before the hello", async () => {
    send(ERROR, { source: frame.contentWindow });
    send({ type: "embed.badge", inbox: 2 }, { source: frame.contentWindow });
    await flush();
    expect(events).toEqual([]);
  });

  it("passes on validated reports after the hello, and nothing from another window or origin", async () => {
    send(HELLO, { source: frame.contentWindow });
    await flush();
    events.length = 0;

    send(ERROR, { source: frame.contentWindow });
    send({ type: "embed.badge", inbox: 3 }, { source: frame.contentWindow });
    send({ type: "embed.badge", inbox: 9 }, { source: other.contentWindow });
    send(
      { type: "embed.badge", inbox: 9 },
      { origin: OTHER, source: frame.contentWindow },
    );
    send({ ...ERROR, kind: "event" }, { source: frame.contentWindow });
    send({ type: "embed.badge", inbox: -1 }, { source: frame.contentWindow });
    await flush();

    expect(events.filter((e) => e.kind !== "rejected")).toEqual([
      { kind: "telemetry", message: ERROR },
      { kind: "badge", inbox: 3 },
    ]);
    expect(
      events.filter((e) => e.kind === "rejected").map((e) => e.reason),
    ).toEqual(["source", "origin"]);
  });
});
