// The Console's key hook behind the real omega-embed/1 bridge (requireDoc).
// A load suspends the current document and cancels its mint; a fresh probe's
// proof then resumes that SAME document with a new cancellation signal. The
// document is unchanged, so a refusal it already received stands: the backend
// is asked once per document, not once per load. A new document (Reconnect
// gives a new frame) is asked afresh.
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createEmbedBridge } from "src/sections/omega/embed/bridge";

import { AssertionMintError, MINT_STATE } from "../session-assertion";
import { useConsoleKey } from "../use-console-key";

const CHILD = "http://127.0.0.1:4191";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const TOKEN = "omega_delegate_eyJ.console.sig";

const hello = (doc) => ({
  type: "embed.hello",
  protocol: "omega-embed/1",
  doc,
});
const proof = (doc, challenge) => ({
  ...hello(doc),
  type: "embed.proof",
  challenge,
});

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const refusedMint = () =>
  vi.fn(async () => {
    throw new AssertionMintError({ state: MINT_STATE.REFUSED, status: 403 });
  });

describe("useConsoleKey behind the bridge, across the frame's load", () => {
  let frame;
  let posted;
  let bridge;

  const fromFrame = (data) =>
    act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data,
          origin: CHILD,
          source: frame.contentWindow,
        }),
      );
      await flush();
    });
  const keys = () => posted.filter((p) => p.message.type === "embed.key");
  const challenge = () =>
    posted.filter((p) => p.message.type === "embed.probe").at(-1)?.message
      .challenge;

  // The hook as ConsoleFrame wires it: the bridge hands it the document's
  // cancellation context with every request for keys.
  const start = (mintAssertion) => {
    const view = renderHook(() => useConsoleKey({ mintAssertion }));
    bridge = createEmbedBridge({
      expectedOrigin: CHILD,
      getFrameWindow: () => frame.contentWindow,
      getTokens: (want, context) =>
        view.result.current.getTokens(want, context),
      requireDoc: true,
    });
    return view;
  };
  const load = () =>
    act(async () => {
      bridge.documentLoaded();
      await flush();
    });

  beforeEach(() => {
    frame = document.createElement("iframe");
    document.body.appendChild(frame);
    posted = [];
    vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
      (message, targetOrigin) => posted.push({ message, targetOrigin }),
    );
  });

  afterEach(() => {
    bridge?.dispose();
    bridge = null;
    frame.remove();
  });

  it("asks once when the refused document is proved again after its load", async () => {
    const mintAssertion = refusedMint();
    const view = start(mintAssertion);

    // The Console says hello before its frame has loaded; the exchange refuses.
    await fromFrame(hello(DOC_A));
    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);

    // The frame's load suspends the document; its proof resumes the same one.
    await load();
    expect(challenge()).toBeTruthy();
    await fromFrame(proof(DOC_A, challenge()));
    // The Console keeps saying hello.
    await fromFrame(hello(DOC_A));
    await fromFrame(hello(DOC_A));

    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(keys()).toHaveLength(0);
    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
  });

  it("asks once when the refusal arrives after the load, and the document is proved again", async () => {
    const pending = [];
    const mintAssertion = vi.fn(
      () =>
        new Promise((resolve, reject) => {
          pending.push({ resolve, reject });
        }),
    );
    const view = start(mintAssertion);

    // The first mint is still in flight when the frame's load cancels it.
    await fromFrame(hello(DOC_A));
    await load();
    await fromFrame(proof(DOC_A, challenge()));
    await act(async () => {
      pending[0].reject(
        new AssertionMintError({ state: MINT_STATE.REFUSED, status: 403 }),
      );
      await flush();
    });
    await fromFrame(hello(DOC_A));

    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(keys()).toHaveLength(0);
    expect(view.result.current.mintFailure).toBe(MINT_STATE.REFUSED);
  });

  it("asks an unavailable backend again when the document is proved after its load", async () => {
    const mintAssertion = vi
      .fn()
      .mockRejectedValueOnce(
        new AssertionMintError({ state: MINT_STATE.UNAVAILABLE, status: 503 }),
      )
      .mockResolvedValue({ token: TOKEN });
    const view = start(mintAssertion);

    await fromFrame(hello(DOC_A));
    expect(view.result.current.mintFailure).toBe(MINT_STATE.UNAVAILABLE);

    await load();
    await fromFrame(proof(DOC_A, challenge()));

    expect(mintAssertion).toHaveBeenCalledTimes(2);
    expect(keys()).toHaveLength(1);
    expect(view.result.current.mintFailure).toBeNull();
  });

  it("asks again for a new document, as Reconnect gives a new frame", async () => {
    const mintAssertion = refusedMint();
    const view = start(mintAssertion);

    await fromFrame(hello(DOC_A));
    expect(mintAssertion).toHaveBeenCalledTimes(1);

    // Reconnect: a new frame element, whose Console names a new document.
    mintAssertion.mockImplementation(async () => ({ token: TOKEN }));
    await act(async () => {
      bridge.reset();
    });
    await fromFrame(hello(DOC_B));

    expect(mintAssertion).toHaveBeenCalledTimes(2);
    expect(keys()).toHaveLength(1);
    expect(keys()[0].message).toMatchObject({ token: TOKEN, doc: DOC_B });
    expect(view.result.current.mintFailure).toBeNull();
  });

  it("gives the proved document its own key after a successful first mint", async () => {
    const mintAssertion = vi.fn(async () => ({ token: TOKEN }));
    start(mintAssertion);

    await fromFrame(hello(DOC_A));
    expect(keys()).toHaveLength(1);

    await load();
    await fromFrame(proof(DOC_A, challenge()));

    // A key is never reused: the proof's answer is a fresh mint.
    expect(mintAssertion).toHaveBeenCalledTimes(2);
    expect(keys()).toHaveLength(2);
  });
});
