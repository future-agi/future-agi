import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEmbedBridge } from "../bridge";

// Built at runtime so the linter does not read it as code.
const SCRIPT_URL = ["javascript", "alert(1)"].join(":");

const CHILD = "http://127.0.0.1:4191";
const THIRD = "http://127.0.0.1:4192";
const HELLO = { type: "embed.hello", protocol: "omega-embed/1" };

const makeFrame = () => {
  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  return iframe;
};

// jsdom delivers MessageEvents synchronously when dispatched on window.
const send = (data, { origin = CHILD, source } = {}) =>
  window.dispatchEvent(new MessageEvent("message", { data, origin, source }));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("omega-embed/1 bridge (parent side)", () => {
  let frame;
  let other;
  let posted;
  let events;
  let bridge;

  const start = (options = {}) => {
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getToken: () => "tok_secret",
      onEvent: (event) => events.push(event),
      ...options,
    });
    return bridge;
  };

  beforeEach(() => {
    frame = makeFrame();
    other = makeFrame();
    posted = [];
    events = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
      (message, targetOrigin) => posted.push({ message, targetOrigin }),
    );
    vi.spyOn(other.contentWindow, "postMessage").mockImplementation(() => {
      throw new Error("the bridge must never write to another frame");
    });
  });

  afterEach(() => {
    bridge?.dispose();
    bridge = null;
    frame.remove();
    other.remove();
    vi.restoreAllMocks();
  });

  it("sends the key only after hello, to the exact expected origin", async () => {
    start();
    expect(posted).toEqual([]);

    send(HELLO, { source: frame.contentWindow });
    await flush();

    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "tok_secret" },
        targetOrigin: CHILD,
      },
    ]);
    expect(events.map((e) => e.kind)).toEqual(["hello", "key-sent"]);
    expect(bridge.isOpen()).toBe(true);
  });

  it("ignores a hello from the right frame but the wrong origin", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken });

    send(HELLO, { origin: THIRD, source: frame.contentWindow });
    await flush();

    expect(getToken).not.toHaveBeenCalled();
    expect(posted).toEqual([]);
    expect(events).toEqual([
      {
        kind: "rejected",
        reason: "origin",
        origin: THIRD,
        type: "embed.hello",
      },
    ]);
  });

  it("ignores a hello from another window that claims the expected origin", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken });

    send(HELLO, { origin: CHILD, source: other.contentWindow });
    send(HELLO, { origin: CHILD, source: window });
    await flush();

    expect(getToken).not.toHaveBeenCalled();
    expect(posted).toEqual([]);
    expect(
      events.every((e) => e.kind === "rejected" && e.reason === "source"),
    ).toBe(true);
  });

  it("ignores malformed hellos and unknown protocols", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken });
    const source = frame.contentWindow;

    send({ type: "embed.hello", protocol: "omega-embed/2" }, { source });
    send({ type: "embed.hello" }, { source });
    send("embed.hello", { source });
    send(null, { source });
    await flush();

    expect(getToken).not.toHaveBeenCalled();
    expect(posted).toEqual([]);
  });

  it("hands a one-shot token out once; a second hello gets no key", async () => {
    let token = "tok_once";
    const getToken = vi.fn(() => {
      const value = token;
      token = null;
      return value;
    });
    start({ getToken });
    const source = frame.contentWindow;

    send(HELLO, { source });
    await flush();
    send(HELLO, { source });
    await flush();

    expect(getToken).toHaveBeenCalledTimes(2);
    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      1,
    );
    expect(events.map((e) => e.kind)).toEqual([
      "hello",
      "key-sent",
      "hello",
      "no-key",
    ]);
  });

  it("waits for an async token and drops it if the frame changed meanwhile", async () => {
    let release;
    const getToken = () => new Promise((resolve) => (release = resolve));
    let current = frame.contentWindow;
    start({ getToken, getFrameWindow: () => current });

    send(HELLO, { source: frame.contentWindow });
    current = other.contentWindow; // the frame navigated while minting
    release("tok_late");
    await flush();

    expect(posted).toEqual([]);
    expect(events.map((e) => e.kind)).toEqual(["hello"]);
  });

  it("ignores route, open and gate messages until a hello", async () => {
    start();
    const source = frame.contentWindow;

    send({ type: "embed.route", path: "/runs/1" }, { source });
    send({ type: "embed.gate", state: "ready" }, { source });
    send({ type: "embed.open", href: "/dashboard/observe" }, { source });
    await flush();

    expect(events).toEqual([]);
  });

  it("reports routes, marking the first after each hello", async () => {
    start();
    const source = frame.contentWindow;
    send(HELLO, { source });
    await flush();

    send({ type: "embed.route", path: "/runs/1" }, { source });
    send({ type: "embed.route", path: "/runs/2?tab=trace" }, { source });
    send({ type: "embed.route", path: "//evil.example/x" }, { source });
    send({ type: "embed.route", path: "https://evil.example/" }, { source });

    expect(events.filter((e) => e.kind === "route")).toEqual([
      { kind: "route", path: "/runs/1", first: true },
      { kind: "route", path: "/runs/2?tab=trace", first: false },
    ]);
  });

  it("opens only hrefs on the parent's own origin", async () => {
    start();
    const source = frame.contentWindow;
    send(HELLO, { source });
    await flush();

    const own = window.location.origin;
    send({ type: "embed.open", href: "/dashboard/observe?x=1" }, { source });
    send({ type: "embed.open", href: `${own}/dashboard/evals` }, { source });
    send({ type: "embed.open", href: "https://evil.example/" }, { source });
    send({ type: "embed.open", href: SCRIPT_URL }, { source });
    send({ type: "embed.open", href: `${CHILD}/runs` }, { source });

    const opened = events
      .filter((e) => e.kind === "open")
      .map((e) => e.url.href);
    expect(opened).toEqual([
      `${own}/dashboard/observe?x=1`,
      `${own}/dashboard/evals`,
    ]);
    expect(events.filter((e) => e.reason === "open")).toHaveLength(3);
  });

  it("passes gate states through and ignores unknown ones", async () => {
    start();
    const source = frame.contentWindow;
    send(HELLO, { source });
    await flush();

    ["needs-key", "refused", "ready", "bogus"].forEach((state) =>
      send({ type: "embed.gate", state }, { source }),
    );

    expect(events.filter((e) => e.kind === "gate").map((e) => e.state)).toEqual(
      ["needs-key", "refused", "ready"],
    );
  });

  it("holds theme and navigate until the hello, then sends both to the child origin", async () => {
    start();
    bridge.setTheme("dark");
    bridge.navigate("/runs/7");
    expect(posted).toEqual([]);

    send(HELLO, { source: frame.contentWindow });
    await flush();

    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "tok_secret" },
        targetOrigin: CHILD,
      },
      { message: { type: "embed.theme", mode: "dark" }, targetOrigin: CHILD },
      {
        message: { type: "embed.navigate", path: "/runs/7" },
        targetOrigin: CHILD,
      },
    ]);

    bridge.setTheme("light");
    bridge.navigate("//evil.example");
    bridge.navigate("/runs/8");
    expect(posted.slice(3)).toEqual([
      { message: { type: "embed.theme", mode: "light" }, targetOrigin: CHILD },
      {
        message: { type: "embed.navigate", path: "/runs/8" },
        targetOrigin: CHILD,
      },
    ]);
  });

  it("stops listening after dispose", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken });
    bridge.dispose();

    send(HELLO, { source: frame.contentWindow });
    await flush();

    expect(getToken).not.toHaveBeenCalled();
    expect(events).toEqual([]);
  });

  it("refuses to start without an expected origin", () => {
    expect(() =>
      createEmbedBridge({
        expectedOrigin: "null",
        getFrameWindow: () => frame.contentWindow,
        getToken: () => "x",
      }),
    ).toThrow(/expected origin/);
  });
});
