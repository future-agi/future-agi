// The parent side of the per-document nonce (RL 1275A; fi-security checklist
// for the embed nonce fix). The Console names its document in every hello and
// gate (`doc`, base64url of 16 random bytes, drawn once per document load).
// With `requireDoc` the bridge:
// - answers a hello only when it names a document, and echoes that doc on
//   every key of the answer;
// - counts a gate only when it names the current document;
// - answers before load, but never attributes a later load by arrival order:
//   load suspends the candidate until it answers a fresh host probe;
// - never takes back a doc it has retired, and cancels a mint for one.
// Probe/proof controls exercise the required child extension. The unchanged
// child has no such response; the explicit no-proof control must fail closed.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEmbedBridge } from "../bridge";
import additions from "../omega-embed-1.additions.json";

const CHILD = "http://127.0.0.1:4191";
const THIRD = "http://127.0.0.1:4192";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const DOC_C = "O8lyHZ6-zHLW9A4op2gj6Q";
const DOCS = [DOC_A, DOC_B, DOC_C];

const hello = (doc, extra = {}) => ({
  type: "embed.hello",
  protocol: "omega-embed/1",
  ...(doc === undefined ? {} : { doc }),
  ...extra,
});
const gate = (state, doc) => ({
  type: "embed.gate",
  state,
  ...(doc === undefined ? {} : { doc }),
});

const proof = (doc, challenge, state) => ({
  ...hello(doc),
  type: "embed.proof",
  challenge,
  ...(state === undefined ? {} : { state }),
});

const makeFrame = () => {
  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  return iframe;
};

const send = (data, { origin = CHILD, source } = {}) =>
  window.dispatchEvent(new MessageEvent("message", { data, origin, source }));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// A mint the test finishes by hand.
const deferredMints = () => {
  const pending = [];
  const getToken = vi.fn(() => new Promise((resolve) => pending.push(resolve)));
  return { getToken, pending };
};

describe("omega-embed/1 bridge, document nonce (requireDoc)", () => {
  let frame;
  let other;
  let posted;
  let events;
  let bridge;

  const start = ({ beforeLoad = false, ...options } = {}) => {
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getToken: () => "tok_secret",
      onEvent: (event) => events.push(event),
      requireDoc: true,
      ...options,
    });
    if (!beforeLoad) {
      bridge.documentLoaded();
      events.length = 0;
    }
    return bridge;
  };
  const fromFrame = (data, origin = CHILD) =>
    send(data, { origin, source: frame.contentWindow });
  const keys = () => posted.filter((p) => p.message.type === "embed.key");
  const kinds = () => events.map((e) => e.kind);
  const gates = () => events.filter((e) => e.kind === "gate");
  const challenge = () =>
    posted.filter((p) => p.message.type === "embed.probe").at(-1)?.message
      .challenge;

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
    // No event the bridge reports (they reach the session, its status
    // callbacks and telemetry) ever names a document.
    const reported = JSON.stringify(events);
    for (const doc of DOCS) expect(reported).not.toContain(doc);
    bridge?.dispose();
    bridge = null;
    frame.remove();
    other.remove();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("echoes the hello's doc on every key of the answer, to the exact origin only", async () => {
    start({ getTokens: vi.fn(async () => ["k1", "k2", "k3"]) });

    fromFrame(hello(DOC_A, { want: 3 }));
    await flush();

    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "k1", left: 2, doc: DOC_A },
        targetOrigin: CHILD,
      },
      {
        message: { type: "embed.key", token: "k2", left: 1, doc: DOC_A },
        targetOrigin: CHILD,
      },
      {
        message: { type: "embed.key", token: "k3", doc: DOC_A },
        targetOrigin: CHILD,
      },
    ]);
    expect(posted.every((p) => p.targetOrigin === CHILD)).toBe(true);
  });

  it("ignores a hello or gate that names no document", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken });

    fromFrame(hello(undefined));
    await flush();
    expect(getToken).not.toHaveBeenCalled();
    expect(posted).toEqual([]);
    expect(kinds()).not.toContain("hello");
    expect(bridge.isOpen()).toBe(false);

    fromFrame(hello(DOC_A));
    await flush();
    expect(keys()).toHaveLength(1);

    fromFrame(gate("ready"));
    expect(gates()).toEqual([]);
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toEqual([{ kind: "gate", state: "ready" }]);
  });

  it("counts a gate only when it names the current document", async () => {
    start();
    fromFrame(hello(DOC_A));
    await flush();

    fromFrame(gate("ready", DOC_C));
    fromFrame(gate("refused", DOC_C));
    expect(gates()).toEqual([]);

    fromFrame(gate("ready", DOC_A));
    expect(gates()).toEqual([{ kind: "gate", state: "ready" }]);
  });

  it("deliberately ignores the published child score and sends no scored answer or telemetry", async () => {
    const contract = additions.messages["embed.score"];
    expect(contract.direction).toBe("child-to-parent");
    expect(additions.messages["embed.scored"].direction).toBe(
      "parent-to-child",
    );
    start();
    fromFrame(hello(DOC_A));
    await flush();
    fromFrame(gate("ready", DOC_A));
    const before = { posted: posted.length, events: events.length };
    fromFrame(contract.example);
    await flush();
    expect(posted).toHaveLength(before.posted);
    expect(events).toHaveLength(before.events);
    expect(JSON.stringify(events)).not.toContain(contract.example.comment);
    expect(bridge.isOpen()).toBe(true);
  });

  it("starts over on the frame's load and never takes the old doc back", async () => {
    start();
    bridge.documentLoaded(); // the first document's load
    fromFrame(hello(DOC_A));
    await flush();
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toHaveLength(1);

    bridge.documentLoaded(); // the frame reloaded in place
    expect(events.at(-1)).toEqual({ kind: "document", cause: "load" });
    expect(bridge.isOpen()).toBe(false);

    // Late messages from the old document change nothing.
    fromFrame(gate("ready", DOC_A));
    fromFrame({ type: "embed.route", path: "/runs/1" });
    fromFrame({ type: "embed.badge", inbox: 3 });
    fromFrame(hello(DOC_A));
    await flush();
    expect(gates()).toHaveLength(1);
    expect(keys()).toHaveLength(1);
    expect(kinds().filter((k) => k === "route" || k === "badge")).toEqual([]);

    // The new document is answered for its own doc.
    fromFrame(hello(DOC_B));
    await flush();
    expect(keys().at(-1).message).toEqual({
      type: "embed.key",
      token: "tok_secret",
      doc: DOC_B,
    });
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toHaveLength(1);
    fromFrame(gate("ready", DOC_B));
    expect(gates()).toHaveLength(2);
  });

  it("answers before load, then revalidates rather than retiring the current doc", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken, beforeLoad: true });
    fromFrame(hello(DOC_A));
    await flush();
    expect(keys()).toHaveLength(1);
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toEqual([{ kind: "gate", state: "ready" }]);

    bridge.documentLoaded();
    expect(bridge.isOpen()).toBe(false);
    expect(challenge()).toMatch(/^[A-Za-z0-9_-]{21}[AQgw]$/);
    fromFrame(gate("ready", DOC_A));
    fromFrame(hello(DOC_A)); // could be queued by an aborted document
    await flush();
    expect(keys()).toHaveLength(1);
    expect(gates()).toHaveLength(1);

    // Required child extension, not a response the unchanged EE child sends.
    fromFrame(proof(DOC_A, challenge(), "ready"));
    await flush();
    expect(keys()).toHaveLength(2);
    expect(bridge.isOpen()).toBe(true);
    expect(gates()).toHaveLength(2);
    fromFrame(hello(DOC_A)); // still eligible for its normal key refills
    await flush();
    expect(keys()).toHaveLength(3);
  });

  it("never consumes a replacement load as an aborted document's missing load", async () => {
    start({ beforeLoad: true });
    fromFrame(hello(DOC_A));
    await flush();
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toHaveLength(1);
    // A's slow resource never finishes: A is aborted, and B loads silently.
    bridge.documentLoaded();
    expect(events.at(-1)).toEqual({ kind: "document", cause: "load" });
    expect(bridge.isOpen()).toBe(false);
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toHaveLength(1);
    expect(keys()).toHaveLength(1); // A was answered before load

    fromFrame(hello(DOC_B));
    await flush();
    fromFrame(hello(DOC_A));
    fromFrame(gate("ready", DOC_A));
    expect(keys()).toHaveLength(2);
    expect(gates()).toHaveLength(1);
    fromFrame(gate("ready", DOC_B));
    expect(gates()).toHaveLength(2);
  });

  it("demotes A immediately on B's pre-load hello and cancels A before reporting it", async () => {
    const getToken = vi.fn(() => "tok_secret");
    let oldSignal;
    start({
      getToken,
      onEvent: (event) => {
        if (event.kind === "document" && oldSignal) {
          expect(oldSignal.aborted).toBe(true);
        }
        events.push(event);
      },
    });
    fromFrame(hello(DOC_A));
    await flush();
    oldSignal = getToken.mock.calls[0][0].signal;
    fromFrame(gate("ready", DOC_A));
    const before = events.length;

    fromFrame(hello(DOC_B)); // no B load, not even later in this test
    expect(bridge.isOpen()).toBe(false);
    expect(events.slice(before).map((event) => event.kind)).toEqual([
      "document",
      "hello",
    ]);
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toHaveLength(1);
    await flush();
    expect(keys().at(-1).message.doc).toBe(DOC_B);
    expect(gates()).toHaveLength(1); // a key is not B's authenticated ready
    fromFrame(gate("ready", DOC_B));
    expect(gates()).toHaveLength(2);
  });

  it("cancels an in-flight A mint on B's early hello without waiting for any load", async () => {
    const { getToken, pending } = deferredMints();
    start({ getToken });
    fromFrame(hello(DOC_A));
    const signal = getToken.mock.calls[0][0].signal;
    fromFrame(hello(DOC_B));
    expect(signal.aborted).toBe(true);
    expect(getToken).toHaveBeenCalledTimes(2);
    pending[0]("tok_obsolete");
    await flush();
    expect(keys()).toEqual([]);
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toEqual([]);
    pending[1]("tok_for_b");
    await flush();
    expect(keys().map((p) => p.message.doc)).toEqual([DOC_B]);
  });

  it("does not borrow an unclaimed load or retire B on B's own load", async () => {
    start({ beforeLoad: true });
    bridge.documentLoaded(); // silent intermediate A; no doc claimed it
    fromFrame(hello(DOC_B));
    await flush();
    fromFrame(gate("ready", DOC_B));
    expect(gates()).toHaveLength(1);
    bridge.documentLoaded(); // B's own load is ambiguous, not a retirement
    expect(bridge.isOpen()).toBe(false);
    fromFrame(proof(DOC_B, challenge(), "ready"));
    await flush();
    expect(bridge.isOpen()).toBe(true);
    expect(gates()).toHaveLength(2);
    fromFrame(hello(DOC_B));
    await flush();
    expect(keys()).toHaveLength(3);
    expect(keys().every((p) => p.message.doc === DOC_B)).toBe(true);
  });

  it("answers within the child's 6 s deadline despite load arriving after 6 s", async () => {
    vi.useFakeTimers();
    start({ beforeLoad: true });
    const childProof = new AbortController();
    let authenticated = false;
    const deadline = setTimeout(() => childProof.abort(), 6000);
    // This models the child's irreversible deadline and authenticated boot,
    // not the real EE hook; only a received matching key can complete it.
    fromFrame(hello(DOC_B));
    await vi.advanceTimersByTimeAsync(0);
    const answer = keys().find((p) => p.message.doc === DOC_B);
    if (answer && !childProof.signal.aborted) {
      authenticated = true;
      clearTimeout(deadline);
      fromFrame(gate("ready", DOC_B));
    }
    await vi.advanceTimersByTimeAsync(6500);
    expect(authenticated).toBe(true);
    expect(childProof.signal.aborted).toBe(false);
    expect(gates()).toHaveLength(1);
    bridge.documentLoaded();
    // The required child extension reaffirms the already authenticated gate.
    fromFrame(proof(DOC_B, challenge(), "ready"));
    await vi.advanceTimersByTimeAsync(0);
    expect(bridge.isOpen()).toBe(true);
    expect(gates()).toHaveLength(2);
  });

  it("fails closed when the unchanged child cannot answer a post-load probe", async () => {
    start({ beforeLoad: true });
    fromFrame(hello(DOC_A));
    await flush();
    fromFrame(gate("ready", DOC_A));
    bridge.documentLoaded(); // indistinguishable from silent B's load
    fromFrame(gate("ready", DOC_A));
    fromFrame(hello(DOC_A));
    await flush();
    expect(bridge.isOpen()).toBe(false);
    expect(gates()).toHaveLength(1);
    expect(keys()).toHaveLength(1);
  });

  it("rejects wrong, malformed, foreign-source and replayed load proofs", async () => {
    start();
    fromFrame(hello(DOC_A));
    await flush();
    bridge.documentLoaded();
    const first = challenge();
    bridge.documentLoaded();
    const second = challenge();
    expect(second).not.toBe(first);
    for (const response of [
      proof(DOC_A, first, "ready"),
      proof(DOC_A, "bad", "ready"),
      proof(DOC_A, second, "not-a-gate"),
      { ...proof(DOC_A, second, "ready"), extra: "no" },
    ])
      fromFrame(response);
    send(proof(DOC_A, second, "ready"), {
      source: other.contentWindow,
      origin: CHILD,
    });
    fromFrame(proof(DOC_A, second, "ready"), THIRD);
    await flush();
    expect(bridge.isOpen()).toBe(false);
    expect(keys()).toHaveLength(1);
    expect(gates()).toEqual([]);
    fromFrame(proof(DOC_A, second, "ready"));
    await flush();
    expect(bridge.isOpen()).toBe(true);
    expect(gates()).toHaveLength(1);
    fromFrame(proof(DOC_A, second, "ready"));
    await flush();
    expect(keys()).toHaveLength(2); // consumed exactly once
    fromFrame(hello(DOC_B));
    await flush();
    bridge.documentLoaded();
    fromFrame(proof(DOC_A, challenge(), "ready")); // retired even with a fresh challenge
    await flush();
    expect(bridge.isOpen()).toBe(false);
    expect(keys()).toHaveLength(3);
    fromFrame(proof(DOC_B, challenge(), "ready"));
    await flush();
    expect(bridge.isOpen()).toBe(true);
  });

  it("stays suspended if a fresh challenge cannot be drawn, but admits a new doc", async () => {
    start();
    fromFrame(hello(DOC_A));
    await flush();
    vi.spyOn(window.crypto, "getRandomValues").mockImplementation(() => {
      throw new Error("entropy unavailable");
    });
    bridge.documentLoaded();
    expect(challenge()).toBeUndefined();
    fromFrame(hello(DOC_A));
    fromFrame(gate("ready", DOC_A));
    await flush();
    expect(bridge.isOpen()).toBe(false);
    expect(keys()).toHaveLength(1);
    expect(gates()).toEqual([]);
    fromFrame(hello(DOC_B));
    await flush();
    fromFrame(gate("ready", DOC_B));
    expect(bridge.isOpen()).toBe(true);
    expect(gates()).toHaveLength(1);
  });

  it("does not replay a proof's cached ready over a newer refused gate during its mint", async () => {
    const { getToken, pending } = deferredMints();
    start({ getToken });
    fromFrame(hello(DOC_A));
    pending[0]("tok_for_a");
    await flush();
    bridge.documentLoaded();
    fromFrame(proof(DOC_A, challenge(), "ready"));
    fromFrame(gate("refused", DOC_A));
    pending[1]("tok_refill");
    await flush();
    expect(keys()).toHaveLength(2);
    expect(gates()).toEqual([{ kind: "gate", state: "refused" }]);
  });

  it("does not restore cached ready if its proof's mint fails", async () => {
    const getToken = vi
      .fn()
      .mockReturnValueOnce("tok_for_a")
      .mockReturnValue(null);
    start({ getToken });
    fromFrame(hello(DOC_A));
    await flush();
    bridge.documentLoaded();
    fromFrame(proof(DOC_A, challenge(), "ready"));
    await flush();
    expect(keys()).toHaveLength(1);
    expect(gates()).toEqual([]);
    expect(kinds().at(-1)).toBe("no-key");
  });

  it("restores a proved ready after a failed remint and successful refill without another ready", async () => {
    const getToken = vi
      .fn()
      .mockReturnValueOnce("tok_for_a")
      .mockReturnValueOnce(null)
      .mockReturnValueOnce("tok_recovered");
    start({ getToken });
    fromFrame(hello(DOC_A));
    await flush();
    fromFrame(gate("ready", DOC_A));
    bridge.documentLoaded();
    events.length = 0;
    const consumed = challenge();
    fromFrame(proof(DOC_A, consumed, "ready"));
    await flush();
    expect(gates()).toEqual([]);
    expect(keys()).toHaveLength(1);
    expect(kinds().at(-1)).toBe("no-key");

    // The real child stays ready and only requests refill keys. It does not
    // repeat its deduplicated ready or answer the consumed challenge again.
    fromFrame(hello(DOC_A));
    await flush();
    expect(keys().at(-1).message.token).toBe("tok_recovered");
    expect(gates()).toEqual([{ kind: "gate", state: "ready" }]);
    fromFrame(proof(DOC_A, consumed, "ready"));
    await flush();
    expect(getToken).toHaveBeenCalledTimes(3);
    expect(gates()).toHaveLength(1);
  });

  it.each(["refused", "needs-key"])(
    "retains a newer %s between failed proof mint and successful recovery",
    async (state) => {
      const { getToken, pending } = deferredMints();
      start({ getToken });
      fromFrame(hello(DOC_A));
      pending[0]("tok_for_a");
      await flush();
      bridge.documentLoaded();
      fromFrame(proof(DOC_A, challenge(), "ready"));
      pending[1](null);
      await flush();
      fromFrame(gate(state, DOC_A));
      expect(gates().at(-1)).toEqual({ kind: "gate", state });
      fromFrame(hello(DOC_A));
      pending[2]("tok_recovered");
      await flush();
      expect(gates().at(-1)).toEqual({ kind: "gate", state });
      expect(gates()).not.toContainEqual({ kind: "gate", state: "ready" });
    },
  );

  it("does not let ready bypass a failed revalidation mint, and uses newer gates during retry", async () => {
    const { getToken, pending } = deferredMints();
    start({ getToken });
    fromFrame(hello(DOC_A));
    pending[0]("tok_for_a");
    await flush();
    bridge.documentLoaded();
    fromFrame(proof(DOC_A, challenge(), "ready"));
    pending[1](null);
    await flush();
    fromFrame(gate("ready", DOC_A));
    expect(gates()).toEqual([]);
    fromFrame(hello(DOC_A));
    fromFrame(gate("refused", DOC_A));
    pending[2]("tok_recovered");
    await flush();
    expect(gates().at(-1)).toEqual({ kind: "gate", state: "refused" });
    expect(gates()).not.toContainEqual({ kind: "gate", state: "ready" });
  });

  it.each(["load", "replacement", "reset"])(
    "clears failed revalidation on %s instead of carrying ready into another admission",
    async (change) => {
      const getToken = vi
        .fn()
        .mockReturnValueOnce("tok_for_a")
        .mockReturnValueOnce(null)
        .mockReturnValue("tok_new");
      start({ getToken });
      fromFrame(hello(DOC_A));
      await flush();
      bridge.documentLoaded();
      fromFrame(proof(DOC_A, challenge(), "ready"));
      await flush();
      expect(gates()).toEqual([]);
      if (change === "load") {
        bridge.documentLoaded();
        fromFrame(hello(DOC_A));
        await flush();
        expect(keys()).toHaveLength(1);
        // A new live proof without a gate cannot borrow the old ready.
        fromFrame(proof(DOC_A, challenge()));
      } else {
        if (change === "reset") bridge.reset();
        fromFrame(hello(DOC_B));
      }
      await flush();
      expect(keys()).toHaveLength(2);
      expect(gates()).toEqual([]);
    },
  );

  it("retains every retired nonce beyond the former 32-document replay boundary", async () => {
    start();
    fromFrame(hello(DOC_A));
    await flush();
    for (let generation = 0; generation < 40; generation += 1) {
      bridge.documentLoaded();
      const doc = `B${String(generation).padStart(20, "0")}A`;
      fromFrame(hello(doc));
      await flush();
    }
    // Replay both while a replacement is current AND while awaiting its hello.
    for (const afterLoad of [false, true]) {
      if (afterLoad) bridge.documentLoaded();
      const before = keys().length;
      fromFrame(hello(DOC_A));
      await flush();
      fromFrame(gate("ready", DOC_A));
      expect(keys()).toHaveLength(before);
      expect(gates()).toEqual([]);
    }
    fromFrame(hello(DOC_C));
    await flush();
    fromFrame(gate("ready", DOC_C));
    expect(gates()).toEqual([{ kind: "gate", state: "ready" }]);
  });

  it("drops a key minted for a document that is gone", async () => {
    const { getToken, pending } = deferredMints();
    start({ getToken });

    fromFrame(hello(DOC_A));
    bridge.documentLoaded(); // a reload while its key was being minted
    pending[0]("tok_for_a");
    await flush();
    expect(keys()).toEqual([]);
    expect(kinds()).not.toContain("key-sent");
  });

  it("answers the new document while a key for the old one is still being minted", async () => {
    const { getToken, pending } = deferredMints();
    start({ getToken });

    fromFrame(hello(DOC_A));
    const oldSignal = getToken.mock.calls[0][0]?.signal;
    bridge.documentLoaded();
    expect(oldSignal?.aborted).toBe(true);
    fromFrame(hello(DOC_B)); // a new document, before A's mint returned
    await flush();
    expect(getToken).toHaveBeenCalledTimes(2);
    expect(getToken.mock.calls[1][0]?.signal?.aborted).toBe(false);

    pending[0]("tok_for_a");
    await flush();
    expect(keys()).toEqual([]);
    pending[1]("tok_for_b");
    await flush();
    expect(keys().map((p) => p.message)).toEqual([
      { type: "embed.key", token: "tok_for_b", doc: DOC_B },
    ]);
  });

  it("takes the current doc only from the frame's own window at its exact origin", async () => {
    const getToken = vi.fn(() => "tok_secret");
    start({ getToken });
    fromFrame(hello(DOC_A));
    await flush();
    expect(getToken).toHaveBeenCalledTimes(1);

    // A sibling frame, the host window itself (what an opener or a popup's
    // window.opener is), and a second sibling standing in for a popup, each
    // claiming the frame's origin and the current doc.
    const popup = makeFrame();
    for (const source of [other.contentWindow, window, popup.contentWindow]) {
      send(gate("ready", DOC_A), { origin: CHILD, source });
      send(hello(DOC_A), { origin: CHILD, source });
    }
    // The frame's window, but another origin.
    fromFrame(gate("ready", DOC_A), THIRD);
    fromFrame(hello(DOC_A), THIRD);
    fromFrame(gate("ready", DOC_A), "null");
    await flush();
    popup.remove();

    expect(gates()).toEqual([]);
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(keys()).toHaveLength(1);
    expect(
      events
        .filter((e) => e.kind === "rejected")
        .map((e) => e.reason)
        .sort(),
    ).toEqual([
      "origin",
      "origin",
      "origin",
      "source",
      "source",
      "source",
      "source",
      "source",
      "source",
    ]);
  });

  it("forgets the document on reconnect (a new frame element)", async () => {
    start();
    fromFrame(hello(DOC_A));
    await flush();
    bridge.reset();

    fromFrame(hello(DOC_A));
    fromFrame(gate("ready", DOC_A));
    await flush();
    expect(keys()).toHaveLength(1);
    expect(gates()).toEqual([]);

    bridge.documentLoaded(); // the replacement frame loads before its hello
    fromFrame(hello(DOC_B));
    await flush();
    expect(keys().at(-1).message.doc).toBe(DOC_B);
  });

  it.each(["reset", "dispose"])(
    "cancels a pending mint on %s",
    async (method) => {
      const { getToken, pending } = deferredMints();
      start({ getToken });
      fromFrame(hello(DOC_A));
      const signal = getToken.mock.calls[0][0]?.signal;
      bridge[method]();
      expect(signal?.aborted).toBe(true);
      pending[0]("tok_obsolete");
      await flush();
      expect(keys()).toEqual([]);
    },
  );
});

describe("omega-embed/1 bridge without requireDoc (Studio)", () => {
  let frame;
  let posted;
  let events;
  let bridge;

  beforeEach(() => {
    frame = makeFrame();
    posted = [];
    events = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
      (message, targetOrigin) => posted.push({ message, targetOrigin }),
    );
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getToken: () => "tok_secret",
      onEvent: (event) => events.push(event),
    });
  });

  afterEach(() => {
    bridge.dispose();
    frame.remove();
    vi.restoreAllMocks();
  });

  it("answers a hello without a doc as before, and a load changes nothing", async () => {
    send(hello(undefined), { source: frame.contentWindow });
    await flush();
    expect(posted).toEqual([
      {
        message: { type: "embed.key", token: "tok_secret" },
        targetOrigin: CHILD,
      },
    ]);

    bridge.documentLoaded();
    expect(bridge.isOpen()).toBe(true);
    send(gate("ready"), { source: frame.contentWindow });
    send(proof(DOC_A, DOC_B, "ready"), { source: frame.contentWindow });
    await flush();
    expect(posted).toHaveLength(1); // no probe or remint for Studio
    expect(events.map((e) => e.kind)).toEqual(["hello", "key-sent", "gate"]);
  });

  it("still echoes a doc that a hello carried", async () => {
    send(hello(DOC_A), { source: frame.contentWindow });
    await flush();
    expect(posted[0].message).toEqual({
      type: "embed.key",
      token: "tok_secret",
      doc: DOC_A,
    });
  });
});
