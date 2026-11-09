// EmbeddedSurface with requireGate AND requireDoc (the EE Console, RL 1275A):
// the frame's document names itself (`doc`) in every hello and gate, and an
// in-place reload of an already connected frame must never stay silently
// "connected". The new document is connected only once ITS gate says ready.
// Without requireDoc (Studio, which sends no doc yet) nothing changes.
import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import EmbeddedSurface from "../EmbeddedSurface";
import { EMBED_STATUS, NO_KEY_GATE_TIMEOUT_MS } from "../use-embed-session";

const CHILD = "http://127.0.0.1:4191";
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const DOC_C = "O8lyHZ6-zHLW9A4op2gj6Q";

const hello = (doc) => ({
  type: "embed.hello",
  protocol: "omega-embed/1",
  ...(doc === undefined ? {} : { doc }),
});
const gate = (state, doc) => ({
  type: "embed.gate",
  state,
  ...(doc === undefined ? {} : { doc }),
});

const surfaceTree = (props = {}) => (
  <ThemeProvider theme={createTheme()}>
    <MemoryRouter initialEntries={["/dashboard/console"]}>
      <Routes>
        <Route
          path="/dashboard/console/*"
          element={
            <EmbeddedSurface
              src={`${CHILD}/?embed=1`}
              basePath="/dashboard/console"
              routeMode="path"
              getToken={() => "tok_minted"}
              label="Omega Console"
              noKeyTimeoutMs={0}
              requireGate
              requireDoc
              {...props}
            />
          }
        />
      </Routes>
    </MemoryRouter>
  </ThemeProvider>
);
const renderSurface = (props = {}) => render(surfaceTree(props));

const frameOf = () => screen.getByTestId("omega-embed-frame");
const surfaceState = () =>
  screen.getByTestId("omega-embedded-surface").dataset.state;
const visibility = (element) => window.getComputedStyle(element).visibility;

const spyOnFrame = (frame) => {
  const posted = [];
  vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};
const keysIn = (posted) =>
  posted.filter((p) => p.message.type === "embed.key").map((p) => p.message);

// Delivers a message and lets the bridge's mint (a microtask) finish.
const deliver = (data, { source, origin = CHILD }) =>
  act(async () => {
    window.dispatchEvent(new MessageEvent("message", { data, origin, source }));
    await Promise.resolve();
    await Promise.resolve();
  });
const fromChild = (frame, data) =>
  deliver(data, { source: frame.contentWindow });

// A load has no doc. It suspends readiness until a fresh probe is answered;
// arrival order must not turn an aborted document's hello into a load credit.
// proofAfterLoad models the REQUIRED child extension, not the unchanged EE.
const frameLoaded = (frame) => act(async () => fireEvent.load(frame));
const reloadInPlace = frameLoaded;
const proofAfterLoad = (frame, posted, doc, state) =>
  fromChild(frame, {
    ...hello(doc),
    type: "embed.proof",
    challenge: posted.filter((p) => p.message.type === "embed.probe").at(-1)
      ?.message.challenge,
    ...(state === undefined ? {} : { state }),
  });

const recordStatuses = () => {
  const seen = [];
  return { seen, onStatusChange: (status) => seen.push(status) };
};

// Connected with document A, whose load came first (the usual order).
const connectA = async (frame) => {
  await frameLoaded(frame);
  await fromChild(frame, hello(DOC_A));
  await fromChild(frame, gate("ready", DOC_A));
  expect(surfaceState()).toBe(EMBED_STATUS.connected);
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("EmbeddedSurface with requireDoc (the Console)", () => {
  it("goes back to connecting when a connected frame reloads in place, and is connected only on the new document's ready", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await connectA(frame);
    expect(visibility(frame)).toBe("visible");

    await reloadInPlace(frame);
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    expect(visibility(frame)).toBe("hidden");

    await fromChild(frame, hello(DOC_B));
    expect(keysIn(posted).at(-1)).toEqual({
      type: "embed.key",
      token: "tok_minted",
      doc: DOC_B,
    });
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    expect(visibility(frame)).toBe("hidden");

    await fromChild(frame, gate("ready", DOC_B));
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    expect(visibility(frame)).toBe("visible");
    expect(seen).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.connected,
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.connected,
    ]);
  });

  it("ignores the old document's gate and hello after the reload", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await connectA(frame);
    await reloadInPlace(frame);
    const keysBefore = keysIn(posted).length;

    await fromChild(frame, gate("ready", DOC_A));
    await fromChild(frame, hello(DOC_A));
    await fromChild(frame, gate("ready", DOC_A));
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    expect(keysIn(posted)).toHaveLength(keysBefore);
    expect(seen.at(-1)).toBe(EMBED_STATUS.connecting);
  });

  it("ignores a gate that names another document", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    spyOnFrame(frame);

    await frameLoaded(frame);
    await fromChild(frame, hello(DOC_A));
    await fromChild(frame, gate("ready", DOC_C));
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    await fromChild(frame, gate("refused", DOC_C));
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    expect(seen).not.toContain(EMBED_STATUS.connected);
  });

  it("ignores a hello or gate that names no document", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, hello(undefined));
    expect(keysIn(posted)).toEqual([]);
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await fromChild(frame, gate("ready"));
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    expect(seen).not.toContain(EMBED_STATUS.connected);
    expect(visibility(frame)).toBe("hidden");
  });

  it("answers before a slow load exceeds the child's 6 s deadline, then requires live load proof", async () => {
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    // A missing opening answer cannot be fixed by injecting a ready later:
    // model the child's irreversible proof abort, then authenticate only if
    // it received its key before that deadline. Real EE-hook coverage is a gap.
    const childProof = new AbortController();
    const deadline = setTimeout(() => childProof.abort(), 6000);
    await fromChild(frame, hello(DOC_B));
    expect(keysIn(posted).map((key) => key.doc)).toEqual([DOC_B]);
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    if (
      keysIn(posted).some((key) => key.doc === DOC_B) &&
      !childProof.signal.aborted
    ) {
      clearTimeout(deadline);
      await fromChild(frame, gate("ready", DOC_B));
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6500);
    });
    expect(childProof.signal.aborted).toBe(false);
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    await frameLoaded(frame);
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await fromChild(frame, gate("ready", DOC_B)); // uncorrelated/queued
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await proofAfterLoad(frame, posted, DOC_B, "ready");
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    expect(seen.slice(0, 3)).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.connected,
    ]);
  });

  it("reconnects after a failed proof mint and successful refill without a second child ready", async () => {
    const getToken = vi
      .fn()
      .mockReturnValueOnce("tok_minted")
      .mockReturnValueOnce(null)
      .mockReturnValueOnce("tok_recovered");
    renderSurface({ getToken });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await connectA(frame);
    await frameLoaded(frame);
    await proofAfterLoad(frame, posted, DOC_A, "ready");
    await waitFor(() => expect(surfaceState()).toBe(EMBED_STATUS.needsKey));
    expect(visibility(frame)).toBe("hidden");
    expect(keysIn(posted)).toHaveLength(1);

    // This is only a refill request. The live child does not change phase or
    // repeat ready; the bridge must retain its safely revalidated verdict.
    await fromChild(frame, hello(DOC_A));
    expect(keysIn(posted)).toHaveLength(2);
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    expect(visibility(frame)).toBe("visible");
    expect(frameOf()).toBe(frame);
  });

  it("ends A's connected state on B's hello even if B's load never arrives", async () => {
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await connectA(frame);
    const beforeB = seen.length;
    await fromChild(frame, hello(DOC_B));
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    expect(visibility(frame)).toBe("hidden");
    expect(keysIn(posted).at(-1).doc).toBe(DOC_B);
    await fromChild(frame, gate("ready", DOC_A));
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS + 100);
    });
    expect(surfaceState()).toBe(EMBED_STATUS.needsKey);
    expect(seen.slice(beforeB)).not.toContain(EMBED_STATUS.connected);
  });

  it("does not retire B after an unclaimed A load and B's own later load", async () => {
    renderSurface();
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await frameLoaded(frame); // silent A
    await fromChild(frame, hello(DOC_B));
    await fromChild(frame, gate("ready", DOC_B));
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    await frameLoaded(frame); // B's load, no reusable credit from A
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await proofAfterLoad(frame, posted, DOC_B, "ready");
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    await fromChild(frame, hello(DOC_B));
    expect(keysIn(posted)).toHaveLength(3);
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
  });

  it("does not inherit readiness when a pre-load document is aborted by a silent replacement", async () => {
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, helloTimeoutMs: 6000 });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await fromChild(frame, hello(DOC_A));
    await fromChild(frame, gate("ready", DOC_A));
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    const afterA = seen.length;
    // A never completes load. The only load delivered belongs to silent B.
    await reloadInPlace(frame);
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await fromChild(frame, gate("ready", DOC_A));
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    expect(keysIn(posted)).toHaveLength(1); // eager A, no answer from B
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6100);
    });
    expect(surfaceState()).toBe(EMBED_STATUS.timeout);
    expect(seen.slice(afterA)).not.toContain(EMBED_STATUS.connected);
  });

  it("does not shift load ownership across multiple early replacement hellos", async () => {
    renderSurface();
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await connectA(frame);
    await fromChild(frame, hello(DOC_B));
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    await fromChild(frame, hello(DOC_C));
    expect(keysIn(posted)).toHaveLength(3);
    await reloadInPlace(frame); // B was aborted; this is C's load
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await fromChild(frame, gate("ready", DOC_A));
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await proofAfterLoad(frame, posted, DOC_C);
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    await fromChild(frame, gate("ready", DOC_C));
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    await reloadInPlace(frame); // a silent D, not a missing load for C
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await fromChild(frame, gate("ready", DOC_C));
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
  });

  it("never shows a reloaded frame that does not sign in as connected", async () => {
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, helloTimeoutMs: 6000 });
    const frame = frameOf();
    spyOnFrame(frame);
    await connectA(frame);
    const afterConnect = seen.length;

    // A reloaded document that never says hello.
    await reloadInPlace(frame);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS * 2);
    });
    expect(surfaceState()).not.toBe(EMBED_STATUS.connected);
    expect(seen.slice(afterConnect)).not.toContain(EMBED_STATUS.connected);

    // Another reload: a document that takes a key but never confirms it.
    await reloadInPlace(frame);
    await fromChild(frame, hello(DOC_B));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS + 100);
    });
    expect(surfaceState()).toBe(EMBED_STATUS.needsKey);
    expect(seen.slice(afterConnect)).not.toContain(EMBED_STATUS.connected);
  });

  it("looks for why a reloaded frame never says hello", async () => {
    vi.useFakeTimers();
    renderSurface({ helloTimeoutMs: 6000 });
    const frame = frameOf();
    spyOnFrame(frame);
    await connectA(frame);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    await reloadInPlace(frame);
    // The no-hello wait starts again for the new document.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5900);
    });
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    vi.useRealTimers();
    await waitFor(() => expect(surfaceState()).toBe(EMBED_STATUS.timeout));
  });

  it("remembers the frame's load when the bridge is started again for a new origin", async () => {
    // The bridge is rebuilt when the expected origin changes; the frame's
    // document (and its load event) stays. Its next reload must still count.
    const { seen, onStatusChange } = recordStatuses();
    const OTHER = "http://127.0.0.1:4193";
    const view = renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);
    await frameLoaded(frame);
    view.rerender(surfaceTree({ onStatusChange, expectedOrigin: OTHER }));

    await deliver(hello(DOC_A), { source: frame.contentWindow, origin: OTHER });
    await deliver(gate("ready", DOC_A), {
      source: frame.contentWindow,
      origin: OTHER,
    });
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    expect(posted.at(-1).targetOrigin).toBe(OTHER);

    await reloadInPlace(frame);
    expect(surfaceState()).toBe(EMBED_STATUS.connecting);
    expect(seen.at(-1)).toBe(EMBED_STATUS.connecting);
  });

  it("does not let another window speak for the frame's new document", async () => {
    renderSurface();
    const frame = frameOf();
    spyOnFrame(frame);
    await connectA(frame);
    await reloadInPlace(frame);
    await fromChild(frame, hello(DOC_B));

    const sibling = document.createElement("iframe");
    document.body.appendChild(sibling);
    vi.spyOn(sibling.contentWindow, "postMessage").mockImplementation(() => {
      throw new Error("never write to another frame");
    });
    // The new doc, from a same-origin window that is not the frame.
    await deliver(gate("ready", DOC_B), { source: sibling.contentWindow });
    await deliver(gate("ready", DOC_B), { source: window });
    // The frame's window, but not its origin.
    await deliver(gate("ready", DOC_B), {
      source: frame.contentWindow,
      origin: "http://127.0.0.1:4192",
    });
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    sibling.remove();
  });
});

describe("EmbeddedSurface without requireDoc (Studio)", () => {
  it("still connects a child that sends no doc, and a load changes nothing", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({
      onStatusChange,
      requireDoc: false,
      noKeyTimeoutMs: undefined,
      getToken: () => "tok_from_a_link",
    });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, hello(undefined));
    expect(posted[0]).toEqual({
      message: { type: "embed.key", token: "tok_from_a_link" },
      targetOrigin: CHILD,
    });
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    await reloadInPlace(frame);
    await fromChild(frame, gate("ready"));
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    await reloadInPlace(frame);
    expect(surfaceState()).toBe(EMBED_STATUS.connected);
    expect(seen).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.connected,
    ]);
  });
});
