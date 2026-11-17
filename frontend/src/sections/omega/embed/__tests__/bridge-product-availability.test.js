import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEmbedBridge } from "../bridge";

const CHILD = "http://127.0.0.1:4191";
const OTHER = "http://127.0.0.1:4192";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const hello = (doc = DOC_A) => ({
  type: "embed.hello",
  protocol: "omega-embed/1",
  doc,
});
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const availabilityPost = (sandboxes, targetOrigin = CHILD) => ({
  message: {
    type: "product.availability",
    version: 1,
    products: { sandboxes },
  },
  targetOrigin,
});

describe("parent product.availability contract", () => {
  let frame;
  let other;
  let posted;
  let bridge;

  const start = (options = {}) => {
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getToken: () => "tok_fixture",
      requireDoc: true,
      ...options,
    });
  };
  // Optional access makes RED at the frozen base an absent-message assertion,
  // rather than a missing-method/import failure.
  const setAvailability = (value) => bridge.setProductAvailability?.(value);
  const availability = () =>
    posted.filter((entry) => entry.message.type === "product.availability");
  const send = (data, { source = frame.contentWindow, origin = CHILD } = {}) =>
    window.dispatchEvent(new MessageEvent("message", { data, source, origin }));
  const connect = async (doc = DOC_A) => {
    send(hello(doc));
    await flush();
  };

  beforeEach(() => {
    frame = document.createElement("iframe");
    other = document.createElement("iframe");
    document.body.append(frame, other);
    posted = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
      (message, targetOrigin) => posted.push({ message, targetOrigin }),
    );
    vi.spyOn(other.contentWindow, "postMessage").mockImplementation(() => {
      throw new Error("never post to another frame");
    });
  });

  afterEach(() => {
    bridge?.dispose();
    frame.remove();
    other.remove();
    vi.restoreAllMocks();
  });

  it.each(["admitted", "paused", "withheld", "unknown"])(
    "posts exactly the %s payload once per established document, after key/theme/navigation",
    async (value) => {
      start();
      setAvailability(value);
      bridge.setTheme("dark");
      bridge.navigate("/agents");
      expect(posted).toEqual([]);

      await connect();
      expect(posted).toEqual([
        {
          message: { type: "embed.key", token: "tok_fixture", doc: DOC_A },
          targetOrigin: CHILD,
        },
        { message: { type: "embed.theme", mode: "dark" }, targetOrigin: CHILD },
        {
          message: { type: "embed.navigate", path: "/agents" },
          targetOrigin: CHILD,
        },
        availabilityPost(value),
      ]);
      setAvailability(value);
      await connect(); // a key refill is not a new session
      expect(availability()).toEqual([availabilityPost(value)]);

      await connect(DOC_B);
      expect(availability()).toEqual([
        availabilityPost(value),
        availabilityPost(value),
      ]);
    },
  );

  it("waits for the mint to finish and flushes only the latest value", async () => {
    let release;
    start({ getToken: () => new Promise((resolve) => (release = resolve)) });
    setAvailability("admitted");
    send(hello());
    setAvailability("withheld");
    expect(posted).toEqual([]);
    release("tok_fixture");
    await flush();
    expect(availability()).toEqual([availabilityPost("withheld")]);
  });

  it("reposts on every value change, but not on an unchanged value or refill", async () => {
    start();
    setAvailability("unknown");
    await connect();
    for (const value of ["admitted", "paused", "withheld", "unknown"]) {
      setAvailability(value);
      setAvailability(value);
      await connect();
    }
    expect(availability()).toEqual(
      ["unknown", "admitted", "paused", "withheld", "unknown"].map((value) =>
        availabilityPost(value),
      ),
    );
  });

  it("uses the existing source, origin, protocol and document checks before posting", async () => {
    start();
    setAvailability("withheld");
    send(hello(), { source: other.contentWindow });
    send(hello(), { source: window });
    send(hello(), { origin: OTHER });
    send({ ...hello(), protocol: "omega-embed/2" });
    send({ type: "embed.hello", protocol: "omega-embed/1" });
    await flush();
    expect(posted).toEqual([]);
    await connect();
    expect(availability()).toEqual([availabilityPost("withheld")]);
  });

  it("holds changes across load suspension until a live document proof establishes the session", async () => {
    start();
    setAvailability("admitted");
    await connect();
    expect(availability()).toEqual([availabilityPost("admitted")]);
    bridge.documentLoaded();
    const challenge = posted
      .filter((entry) => entry.message.type === "embed.probe")
      .at(-1).message.challenge;
    setAvailability("withheld");
    await connect(); // queued hello cannot restore a suspended document
    expect(availability()).toEqual([availabilityPost("admitted")]);
    send({ ...hello(), type: "embed.proof", challenge });
    await flush();
    expect(availability()).toEqual([
      availabilityPost("admitted"),
      availabilityPost("withheld"),
    ]);
    await connect();
    expect(availability()).toHaveLength(2);
  });

  it("resends the current value after reset, but ignores retired documents and disposal", async () => {
    start();
    setAvailability("paused");
    await connect();
    expect(availability()).toEqual([availabilityPost("paused")]);
    bridge.reset();
    setAvailability("withheld");
    await connect();
    expect(availability()).toEqual([availabilityPost("paused")]);
    await connect(DOC_B);
    expect(availability()).toEqual([
      availabilityPost("paused"),
      availabilityPost("withheld"),
    ]);
    bridge.dispose();
    setAvailability("admitted");
    await connect(DOC_B);
    expect(availability()).toHaveLength(2);
  });

  it("does not flush an availability value from a cancelled mint", async () => {
    let release;
    start({ getToken: () => new Promise((resolve) => (release = resolve)) });
    setAvailability("withheld");
    send(hello());
    bridge.documentLoaded();
    release("tok_cancelled");
    await flush();
    expect(availability()).toEqual([]);
    send(hello(DOC_B));
    release("tok_current");
    await flush();
    expect(availability()).toEqual([availabilityPost("withheld")]);
  });

  it("resends an unchanged value when a live proof reestablishes a loaded document", async () => {
    start();
    setAvailability("paused");
    await connect();
    expect(availability()).toEqual([availabilityPost("paused")]);
    bridge.documentLoaded();
    const challenge = posted
      .filter((entry) => entry.message.type === "embed.probe")
      .at(-1).message.challenge;
    send({ ...hello(), type: "embed.proof", challenge });
    await flush();
    expect(availability()).toEqual([
      availabilityPost("paused"),
      availabilityPost("paused"),
    ]);
  });

  it("never copies an invalid value or user data into the exact enum-only payload", async () => {
    start();
    setAvailability("admitted");
    await connect();
    setAvailability({ sandboxes: "withheld", token: "must_not_cross" });
    expect(availability()).toEqual([
      availabilityPost("admitted"),
      availabilityPost("unknown"),
    ]);
  });

  it("uses the established hello boundary even if no key was minted, like theme/navigation", async () => {
    start({ getToken: () => null });
    setAvailability("unknown");
    await connect();
    expect(posted).toEqual([availabilityPost("unknown")]);
  });
});
