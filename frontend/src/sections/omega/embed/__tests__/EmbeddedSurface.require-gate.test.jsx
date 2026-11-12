// EmbeddedSurface with requireGate (Studio): a key handed over is only being
// checked until the child's gate says "ready". The frame stays hidden, the
// panel says it is checking, and nothing reports connected before then.
import React from "react";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import EmbeddedSurface from "../EmbeddedSurface";
import { EMBED_STATUS, NO_KEY_GATE_TIMEOUT_MS } from "../use-embed-session";

const CHILD = "http://127.0.0.1:4191";
const HELLO = { type: "embed.hello", protocol: "omega-embed/1" };

const renderSurface = (props = {}) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MemoryRouter initialEntries={["/dashboard/studio"]}>
        <Routes>
          <Route
            path="/dashboard/studio/*"
            element={
              <EmbeddedSurface
                src={`${CHILD}/?embed=1`}
                basePath="/dashboard/studio"
                getToken={() => "tok_from_a_link"}
                label="local Studio"
                requireGate
                {...props}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const frameOf = () => screen.getByTestId("omega-embed-frame");
const gateState = () => screen.queryByTestId("omega-embed-gate")?.dataset.state;
const surfaceState = () =>
  screen.getByTestId("omega-embedded-surface").dataset.state;

const spyOnFrame = (frame) => {
  const posted = [];
  vi.spyOn(frame.contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};

// Delivers a child message and lets the bridge's mint (a microtask) finish.
const fromChild = (frame, data) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin: CHILD,
        source: frame.contentWindow,
      }),
    );
    await Promise.resolve();
    await Promise.resolve();
  });

const visibility = (element) => window.getComputedStyle(element).visibility;

// Studio's getToken: the link's key, handed out once.
const oneShot = (token) => () => {
  const value = token;
  token = null;
  return value;
};

const recordStatuses = () => {
  const seen = [];
  return { seen, onStatusChange: (status) => seen.push(status) };
};

// A child that says hello every HELLO_EVERY_MS for `ms` and never reports its
// gate; returns the gate state shown after each hello. Needs pure fake timers:
// advanceTimersByTimeAsync lets each hello's key go out before a timer fires.
const HELLO_EVERY_MS = 500;
const helloFor = async (frame, ms) => {
  const states = [];
  for (let elapsed = 0; elapsed < ms; elapsed += HELLO_EVERY_MS) {
    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: HELLO,
          origin: CHILD,
          source: frame.contentWindow,
        }),
      );
      await vi.advanceTimersByTimeAsync(HELLO_EVERY_MS);
    });
    states.push(gateState());
  }
  return states;
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("EmbeddedSurface with requireGate", () => {
  it("checks the key it handed over and is connected only on the child's ready", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, HELLO);
    expect(posted[0]).toEqual({
      message: { type: "embed.key", token: "tok_from_a_link" },
      targetOrigin: CHILD,
    });
    expect(gateState()).toBe(EMBED_STATUS.checking);
    expect(surfaceState()).toBe(EMBED_STATUS.checking);
    expect(screen.getByTestId("omega-embed-gate")).toHaveTextContent(
      "Checking the key with local Studio…",
    );
    // The child's page stays hidden while its key is unproven.
    expect(visibility(frame)).toBe("hidden");

    await fromChild(frame, { type: "embed.gate", state: "ready" });
    expect(gateState()).toBeUndefined();
    expect(visibility(frame)).toBe("visible");
    expect(seen).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.connected,
    ]);
  });

  it("never reports connected for a key the child refuses", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, getToken: oneShot("tok_forged") });
    const frame = frameOf();
    spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await fromChild(frame, HELLO); // a hello sent before the key arrived
    await fromChild(frame, { type: "embed.gate", state: "refused" });

    expect(gateState()).toBe(EMBED_STATUS.refused);
    expect(seen).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.refused,
    ]);
    expect(seen).not.toContain(EMBED_STATUS.connected);
    expect(visibility(frame)).toBe("hidden");
  });

  it("asks for a key again when the child never answers about the key, and never shows connected", async () => {
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, getToken: oneShot("tok_unanswered") });
    const frame = frameOf();
    spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS - 3000);
    });
    // A trailing hello neither ends the check nor pushes the bound out.
    await fromChild(frame, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2900);
    });
    expect(gateState()).toBe(EMBED_STATUS.checking);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(gateState()).toBe(EMBED_STATUS.needsKey);
    expect(seen).not.toContain(EMBED_STATUS.connected);
  });

  it("gives a key it handed over the whole bound even when a hello it could not answer gets none", async () => {
    // The Console's no-key bound is 0: a hello Future AGI could not answer
    // means the mint failed and the reason is already known. A key that was
    // handed over still needs the child's round trip before it can be judged.
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, noKeyTimeoutMs: 0 });
    const frame = frameOf();
    spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS - 100);
    });
    expect(gateState()).toBe(EMBED_STATUS.checking);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(gateState()).toBe(EMBED_STATUS.needsKey);
    expect(seen).not.toContain(EMBED_STATUS.connected);
  });

  it("keeps one bound for a key it cannot get confirmed, however many hellos are handed another key", async () => {
    // The Console's shape: every hello is answered with a fresh key, and its
    // no-key bound is 0. A child that keeps saying hello but never reports its
    // gate is still one unconfirmed attempt: the bound runs from the first key.
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({
      onStatusChange,
      getToken: () => "tok_every_time",
      noKeyTimeoutMs: 0,
    });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    // Hellos at 0 .. 7000 ms; the clock is at 7500 ms.
    const before = await helloFor(
      frame,
      NO_KEY_GATE_TIMEOUT_MS - HELLO_EVERY_MS,
    );
    expect(new Set(before)).toEqual(new Set([EMBED_STATUS.checking]));

    await helloFor(frame, 2 * HELLO_EVERY_MS);
    expect(gateState()).toBe(EMBED_STATUS.needsKey);

    // Later hellos, each handed another key, leave it asking for a key.
    const after = await helloFor(frame, NO_KEY_GATE_TIMEOUT_MS);
    expect(new Set(after)).toEqual(new Set([EMBED_STATUS.needsKey]));

    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      before.length + 2 + after.length,
    );
    expect(seen).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.needsKey,
    ]);
    expect(visibility(frame)).toBe("hidden");
  });

  it("still connects on a ready after that bound, while the child keeps saying hello", async () => {
    vi.useFakeTimers();
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({
      onStatusChange,
      getToken: () => "tok_every_time",
      noKeyTimeoutMs: 0,
    });
    const frame = frameOf();
    spyOnFrame(frame);

    await helloFor(frame, NO_KEY_GATE_TIMEOUT_MS + 2 * HELLO_EVERY_MS);
    expect(gateState()).toBe(EMBED_STATUS.needsKey);

    await fromChild(frame, { type: "embed.gate", state: "ready" });
    expect(gateState()).toBeUndefined();
    expect(visibility(frame)).toBe("visible");
    expect(seen).toEqual([
      EMBED_STATUS.connecting,
      EMBED_STATUS.checking,
      EMBED_STATUS.needsKey,
      EMBED_STATUS.connected,
    ]);
  });

  it("still lets a ready that comes after the bound connect", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, noKeyTimeoutMs: 10 });
    const frame = frameOf();
    spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await act(() => new Promise((resolve) => setTimeout(resolve, 40)));
    expect(gateState()).toBe(EMBED_STATUS.needsKey);
    await fromChild(frame, { type: "embed.gate", state: "ready" });
    expect(gateState()).toBeUndefined();
    expect(seen.at(-1)).toBe(EMBED_STATUS.connected);
  });

  it("stays connected when the child says hello again after it proved the key", async () => {
    // A getToken that answers every hello: a second key must not send a
    // working frame back to checking (the child reports "ready" only once).
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, getToken: () => "tok_every_time" });
    const frame = frameOf();
    const posted = spyOnFrame(frame);

    await fromChild(frame, HELLO);
    await fromChild(frame, { type: "embed.gate", state: "ready" });
    await fromChild(frame, HELLO);

    expect(posted.filter((p) => p.message.type === "embed.key")).toHaveLength(
      2,
    );
    expect(gateState()).toBeUndefined();
    expect(seen.at(-1)).toBe(EMBED_STATUS.connected);
  });

  it("uses the surface's own words for checking", async () => {
    renderSurface({ copy: { checking: "Asking Studio about the key…" } });
    const frame = frameOf();
    spyOnFrame(frame);
    await fromChild(frame, HELLO);
    expect(screen.getByTestId("omega-embed-gate")).toHaveTextContent(
      "Asking Studio about the key…",
    );
  });

  it("without requireGate connects on the key", async () => {
    const { seen, onStatusChange } = recordStatuses();
    renderSurface({ onStatusChange, requireGate: false });
    const frame = frameOf();
    spyOnFrame(frame);
    await fromChild(frame, HELLO);
    expect(gateState()).toBeUndefined();
    expect(seen).toEqual([EMBED_STATUS.connecting, EMBED_STATUS.connected]);
  });
});
