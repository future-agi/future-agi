// The Console page and the per-document nonce (RL 1275A). The EE Console
// names its document (`doc`) in every hello and gate and uses only a key that
// echoes it. ConsoleFrame opts the shared surface into requireDoc, so:
// - a Console hello or gate without a doc is ignored;
// - an in-place reload of a connected Console frame goes back to opening and
//   is connected again only on the new document's "ready";
// - the doc goes back to the frame on its keys and nowhere else: never into
//   a URL, a log line, Sentry, the page or a status report.
import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMBED_STATUS } from "src/sections/omega/embed";
import { resetConsoleInboxForTests } from "src/sections/omega/shell/console-inbox";
import { resetConsoleAccessForTests } from "src/sections/omega/shell/omega-access";

import ConsoleFramePage from "../ConsoleFramePage";
import { AssertionMintError, MINT_STATE } from "../session-assertion";
import { MINT_BACKOFF_MS } from "../use-console-key";

const sentry = vi.hoisted(() => ({
  captureEvent: vi.fn(),
  addBreadcrumb: vi.fn(),
  startInactiveSpan: vi.fn(() => ({ end: () => {} })),
}));
vi.mock("@sentry/react", () => sentry);
const chrome = vi.hoisted(() => ({ setFrameShown: null }));
vi.mock("../console-chrome-link", () => ({
  useConsoleChrome: () => ({
    frameShown: false,
    setFrameShown: chrome.setFrameShown,
  }),
}));

const ORIGIN = window.location.origin;
const DOC_A = "AAECAwQFBgcICQoLDA0ODw";
const DOC_B = "m4gLqkrE04O3BMKxGKKPFg";
const DOCS = [DOC_A, DOC_B];

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
const keys = (n, prefix) =>
  Array.from({ length: n }, (_, i) => `omega_delegate_${prefix}${i}`);

let mints = 0;
const batchMint = () =>
  vi.fn(async ({ count = 1 } = {}) => {
    mints += 1;
    const tokens = keys(count, `m${mints}_`);
    return { token: tokens[0], tokens, expiresAt: 0 };
  });

let currentLocation;
function LocationProbe() {
  currentLocation = useLocation();
  return null;
}

const renderPage = (mint = batchMint()) => {
  render(
    <HelmetProvider>
      <ThemeProvider theme={createTheme()}>
        <MemoryRouter initialEntries={["/dashboard/console"]}>
          <LocationProbe />
          <Routes>
            <Route
              path="/dashboard/console/*"
              element={<ConsoleFramePage mintAssertion={mint} />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
  return mint;
};

const frame = () => screen.getByTestId("omega-embed-frame");
const pageState = () => screen.getByTestId("omega-tab-console").dataset.state;
const spyOnFrame = () => {
  const posted = [];
  vi.spyOn(frame().contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};
const keysIn = (posted) =>
  posted.filter((p) => p.message.type === "embed.key").map((p) => p.message);
const post = (data) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin: ORIGIN,
        source: frame().contentWindow,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
// The frame's load event: one per document (jsdom fires none on its own).
// The first is the first document's; a later one is an in-place reload.
const frameLoaded = () => act(async () => fireEvent.load(frame()));
const reloadInPlace = frameLoaded;
// Required child extension. The current EE child does not answer these probes.
const proofAfterLoad = (posted, doc, state) =>
  post({
    ...hello(doc),
    type: "embed.proof",
    challenge: posted.filter((p) => p.message.type === "embed.probe").at(-1)
      ?.message.challenge,
    ...(state === undefined ? {} : { state }),
  });

// Everything a value may have reached, as text: circular references and
// Errors (React's warnings pass both) are flattened, never thrown on.
const textOf = (value) => {
  const seen = new WeakSet();
  return JSON.stringify(value, (key, item) => {
    if (item instanceof Error) return `${item.message}\n${item.stack}`;
    if (typeof item === "function") return `[fn ${item.name}]`;
    if (item && typeof item === "object") {
      if (seen.has(item)) return "[seen]";
      seen.add(item);
      if (typeof Node !== "undefined" && item instanceof Node) {
        return item.outerHTML ?? item.textContent;
      }
    }
    return item;
  });
};

beforeEach(() => {
  mints = 0;
  chrome.setFrameShown = vi.fn();
  Object.values(sentry).forEach((fn) => fn.mockClear?.());
});

afterEach(() => {
  act(() => {
    resetConsoleInboxForTests();
    resetConsoleAccessForTests();
  });
  vi.restoreAllMocks();
});

describe("Console frame: the document nonce (RL 1275A)", () => {
  it("ignores a Console hello or gate that names no document", async () => {
    const mint = renderPage();
    const posted = spyOnFrame();

    await post(hello(undefined, { want: 2 }));
    expect(mint).not.toHaveBeenCalled();
    expect(keysIn(posted)).toEqual([]);
    await post(gate("ready"));
    expect(pageState()).toBe(EMBED_STATUS.connecting);
    expect(chrome.setFrameShown).not.toHaveBeenCalledWith(true);
  });

  it("goes back to opening when the connected Console reloads in place, and connects only on the new document's ready", async () => {
    const mint = renderPage();
    const posted = spyOnFrame();

    await frameLoaded();
    await post(hello(DOC_A, { want: 2 }));
    expect(mint).toHaveBeenCalledWith({ count: 2 });
    expect(keysIn(posted)).toEqual([
      { type: "embed.key", token: "omega_delegate_m1_0", left: 1, doc: DOC_A },
      { type: "embed.key", token: "omega_delegate_m1_1", doc: DOC_A },
    ]);
    await post(gate("ready", DOC_A));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);

    await reloadInPlace();
    expect(pageState()).toBe(EMBED_STATUS.connecting);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
    expect(screen.getByText("Opening Omega Console…")).toBeInTheDocument();

    // The old document's word no longer counts.
    await post(gate("ready", DOC_A));
    expect(pageState()).toBe(EMBED_STATUS.connecting);

    await post(hello(DOC_B, { want: 2 }));
    expect(keysIn(posted).slice(2)).toEqual([
      { type: "embed.key", token: "omega_delegate_m2_0", left: 1, doc: DOC_B },
      { type: "embed.key", token: "omega_delegate_m2_1", doc: DOC_B },
    ]);
    expect(pageState()).toBe(EMBED_STATUS.checking);
    await post(gate("ready", DOC_B));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(posted.every((p) => p.targetOrigin === ORIGIN)).toBe(true);
  });

  it("recovers a proved ready through the real key hook's transient backoff without a second ready", async () => {
    let now = Date.now();
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const mint = batchMint()
      .mockResolvedValueOnce({ token: "omega_delegate_open" })
      .mockRejectedValueOnce(
        new AssertionMintError({ state: MINT_STATE.UNAVAILABLE, status: 503 }),
      );
    renderPage(mint);
    const posted = spyOnFrame();
    const originalFrame = frame();
    await post(hello(DOC_A));
    await post(gate("ready", DOC_A));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    await frameLoaded();
    await proofAfterLoad(posted, DOC_A, "ready");
    expect(pageState()).not.toBe(EMBED_STATUS.connected);
    expect(keysIn(posted)).toHaveLength(1);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
    expect(mint).toHaveBeenCalledTimes(2);

    // The same document's hello during backoff sends nothing and must not
    // restore ready. Expiry permits one successful refill, not a new boot.
    await post(hello(DOC_A));
    expect(mint).toHaveBeenCalledTimes(2);
    expect(pageState()).not.toBe(EMBED_STATUS.connected);
    now += MINT_BACKOFF_MS[0] + 1;
    await post(hello(DOC_A));
    expect(mint).toHaveBeenCalledTimes(3);
    expect(keysIn(posted)).toHaveLength(2);
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);
    expect(frame()).toBe(originalFrame);
  });

  it.each([MINT_STATE.VIEWER, MINT_STATE.REFUSED, MINT_STATE.UNAVAILABLE])(
    "ignores an obsolete %s mint failure after the replacement connects (real key hook)",
    async (state) => {
      let rejectOld;
      const mint = batchMint().mockImplementationOnce(
        () =>
          new Promise((resolve, reject) => {
            rejectOld = reject;
          }),
      );
      renderPage(mint);
      const posted = spyOnFrame();
      await frameLoaded();
      await post(hello(DOC_A)); // still pending when the document goes away
      expect(mint).toHaveBeenCalledTimes(1);
      await reloadInPlace();
      await post(hello(DOC_B));
      await post(gate("ready", DOC_B));
      expect(pageState()).toBe(EMBED_STATUS.connected);

      await act(async () => {
        rejectOld(new AssertionMintError({ state, status: 403 }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      // In particular a late VIEWER must not replace the live frame with a panel.
      expect(frame()).toBeInTheDocument();
      expect(pageState()).toBe(EMBED_STATUS.connected);
      expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);
      await post(hello(DOC_B)); // its refill must not inherit old refusal/backoff
      expect(mint).toHaveBeenCalledTimes(3);
      expect(keysIn(posted).map((message) => message.doc)).toEqual([
        DOC_B,
        DOC_B,
      ]);
    },
  );

  it("hides A on B's early hello, connects only on B's ready, and revalidates B's load", async () => {
    const mint = renderPage();
    const posted = spyOnFrame();
    await frameLoaded();
    await post(hello(DOC_A));
    await post(gate("ready", DOC_A));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    await post(hello(DOC_B)); // must answer before the child's 6 s deadline
    expect(mint).toHaveBeenCalledTimes(2);
    expect(pageState()).toBe(EMBED_STATUS.checking);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
    await post(gate("ready", DOC_A));
    expect(pageState()).toBe(EMBED_STATUS.checking);
    await post(gate("ready", DOC_B));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    await reloadInPlace();
    expect(pageState()).toBe(EMBED_STATUS.connecting);
    await proofAfterLoad(posted, DOC_B, "ready");
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);
    await post(hello(DOC_B)); // load did not retire this live document
    expect(mint).toHaveBeenCalledTimes(4);
    expect(keysIn(posted).map((message) => message.doc)).toEqual([
      DOC_A,
      DOC_B,
      DOC_B,
      DOC_B,
    ]);
  });

  it.each([MINT_STATE.VIEWER, MINT_STATE.REFUSED, MINT_STATE.UNAVAILABLE])(
    "cancels A's pending refill on B's pre-load hello before its obsolete %s result",
    async (state) => {
      let rejectOld;
      const mint = batchMint();
      renderPage(mint);
      const posted = spyOnFrame();
      await frameLoaded();
      await post(hello(DOC_A));
      await post(gate("ready", DOC_A));
      mint.mockImplementationOnce(
        () =>
          new Promise((resolve, reject) => {
            rejectOld = reject;
          }),
      );
      await post(hello(DOC_A)); // connected A has a pending refill
      await post(hello(DOC_B)); // no load anywhere in this handoff
      expect(pageState()).toBe(EMBED_STATUS.checking);
      expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
      await post(gate("ready", DOC_A));
      expect(pageState()).toBe(EMBED_STATUS.checking);
      await post(gate("ready", DOC_B));
      await act(async () => {
        rejectOld(new AssertionMintError({ state, status: 403 }));
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(frame()).toBeInTheDocument();
      expect(pageState()).toBe(EMBED_STATUS.connected);
      await post(hello(DOC_B));
      expect(mint).toHaveBeenCalledTimes(4);
      expect(keysIn(posted).map((message) => message.doc)).toEqual([
        DOC_A,
        DOC_B,
        DOC_B,
      ]);
    },
  );

  it("does not clear B's signed-out mint result with its own connecting notification", async () => {
    const mint = renderPage();
    const posted = spyOnFrame();
    await frameLoaded();
    await post(hello(DOC_A));
    await post(gate("ready", DOC_A));
    mint.mockRejectedValue(
      new AssertionMintError({ state: MINT_STATE.SIGNED_OUT, status: 401 }),
    );
    await post(hello(DOC_B));
    await post(gate("needs-key", DOC_B));
    expect(pageState()).toBe(EMBED_STATUS.needsKey);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
    await post(hello(DOC_B));
    expect(mint).toHaveBeenCalledTimes(2); // B's held refusal was not erased
    expect(keysIn(posted).map((message) => message.doc)).toEqual([DOC_A]);
    await post(gate("ready", DOC_A));
    expect(pageState()).toBe(EMBED_STATUS.needsKey);
  });

  it("keeps B reconnectable when an earlier silent document loaded without a hello", async () => {
    const mint = renderPage();
    const posted = spyOnFrame();
    await frameLoaded(); // unclaimed A
    await post(hello(DOC_B));
    await post(gate("ready", DOC_B));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    await frameLoaded(); // B's own load
    expect(pageState()).toBe(EMBED_STATUS.connecting);
    await proofAfterLoad(posted, DOC_B, "ready");
    await post(hello(DOC_B));
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(mint).toHaveBeenCalledTimes(3);
    expect(keysIn(posted).every((message) => message.doc === DOC_B)).toBe(true);
  });

  it("never puts the doc in a URL, a log line, Sentry, the page or a status report", async () => {
    const logged = ["log", "info", "warn", "error", "debug", "trace"].map(
      (method) => vi.spyOn(console, method),
    );
    const pushed = vi.spyOn(window.history, "pushState");
    const replaced = vi.spyOn(window.history, "replaceState");
    const hrefBefore = window.location.href;
    renderPage();
    const posted = spyOnFrame();
    const locations = [];

    const record = () =>
      locations.push(
        `${currentLocation.pathname}${currentLocation.search}${currentLocation.hash}`,
        window.location.href,
        document.title,
      );

    await frameLoaded();
    await post(hello(DOC_A, { want: 2, build: "r9-2803ad68" }));
    await post(gate("ready", DOC_A));
    // Every other message the Console sends, each carrying a doc it must not.
    await post({ type: "embed.route", path: "/agents?tab=runs", doc: DOC_A });
    record();
    await post({
      type: "embed.telemetry",
      kind: "error",
      name: "load",
      routeTemplate: "/agents/:agentId",
      release: "r9-2803ad68",
      code: "http_502",
      requestId: "req_6f1c2b9e0a7d4c3b8e5f",
      doc: DOC_A,
    });
    await post({
      type: "embed.telemetry",
      kind: "vital",
      name: "route-data",
      routeTemplate: "/agents",
      release: "r9-2803ad68",
      valueMs: 412,
      doc: DOC_A,
    });
    await post({ type: "embed.badge", inbox: 2, doc: DOC_A });
    record();

    await reloadInPlace();
    await post(gate("ready", DOC_A)); // the old document's, ignored
    await post(hello(DOC_B));
    await post(gate("refused", DOC_B));
    record();

    // The flow ran: the keys echoed each document's doc, the reports went
    // to Sentry, and the route reached the page URL.
    expect(keysIn(posted).map((key) => key.doc)).toEqual([DOC_A, DOC_A, DOC_B]);
    expect(sentry.captureEvent).toHaveBeenCalledTimes(1);
    expect(sentry.addBreadcrumb).toHaveBeenCalled();
    expect(currentLocation.pathname).toBe("/dashboard/console/agents");
    expect(pageState()).toBe(EMBED_STATUS.refused);

    const everywhere = textOf({
      console: logged.map((spy) => spy.mock.calls),
      history: [pushed.mock.calls, replaced.mock.calls],
      sentry: Object.values(sentry).map((fn) => fn.mock.calls),
      locations,
      hrefBefore,
      href: window.location.href,
      frameSrc: frame().getAttribute("src"),
      page: document.documentElement.outerHTML,
      // What the frame got besides its keys.
      notKeys: posted.filter((p) => p.message.type !== "embed.key"),
    });
    for (const doc of DOCS) expect(everywhere).not.toContain(doc);
  });
});
