// A link's one-shot key belongs to the connection that link opened.
//
// While a frame waits for its key, the RC child says hello again every 250 ms.
// When a fresh link arrives by fragment navigation, index.html fires
// omega:connect synchronously from hashchange/popstate, and the tab switches
// to the new server on its next render. A hello from the old frame that lands
// before that render must not take the new link's key: the new frame would be
// left without one, and the key would go to the old server's origin. Under
// React.StrictMode the useState initializer runs twice; neither run may take
// the key, which waits for the frame's hello.
import React from "react";
import PropTypes from "prop-types";
import { act, renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  hasOmegaConnectToken,
  offerOmegaConnect,
} from "src/sections/omega/embed";

import { useStudioConnection } from "../use-studio-connection";
import {
  HELLO,
  SERVER,
  TOKEN,
  frame,
  fromChild,
  planLink,
  renderStudio,
  resetStudioTestState,
  spyOnFrame,
} from "./studio-test-utils";

// A second omega dev on another port: another origin.
const OTHER = "http://127.0.0.1:4300";
// Deterministic, format-valid test fixtures; not generated access tokens.
const FRESH = "fresh-link-fixture-".padEnd(43, "2");
const OFFERED = "offered-fixture-".padEnd(43, "3");
const READY = { type: "embed.gate", state: "ready" };

const keys = (posted) => posted.filter((p) => p.message.type === "embed.key");
const carrying = (posted, token) =>
  posted.filter((p) => JSON.stringify(p.message).includes(token));

function Router({ children }) {
  return (
    <MemoryRouter initialEntries={["/dashboard/studio"]}>
      {children}
    </MemoryRouter>
  );
}
Router.propTypes = { children: PropTypes.node };

// A fresh link arrives while the old frame is still mounted, and the old
// frame's next hello lands in the same task, before React re-renders the tab
// for the new server (and again one tick later, as the child repeats it).
async function freshLinkWhileStaleFrameSaysHello(stale) {
  await act(async () => {
    planLink(OTHER, FRESH);
    window.dispatchEvent(new Event("omega:connect"));
    for (let hello = 0; hello < 2; hello += 1) {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: HELLO,
          origin: SERVER,
          source: stale.contentWindow,
        }),
      );
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  });
}

beforeEach(() => {
  resetStudioTestState();
  globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("offline")));
});

afterEach(() => {
  vi.restoreAllMocks();
  resetStudioTestState();
});

describe("Studio link keys go only to the link's own connection", () => {
  it("a stale link connection cannot take a key offered for a newer generation", () => {
    planLink();
    const tab = renderHook(() => useStudioConnection(), { wrapper: Router });
    const stale = tab.result.current.connection;
    expect(stale).toMatchObject({ source: "link", origin: SERVER });
    // Its own key, once: the hello that connected it.
    expect(stale.getToken()).toBe(TOKEN);
    expect(stale.getToken()).toBeNull();

    // A fresh link by fragment navigation on the open page.
    act(() => {
      planLink(OTHER, FRESH);
      window.dispatchEvent(new Event("omega:connect"));
    });
    const fresh = tab.result.current.connection;
    expect(fresh).toMatchObject({ source: "link", origin: OTHER });
    expect(fresh.id).not.toBe(stale.id);
    // The old frame's repeated hello gets nothing; the new one gets its key.
    expect(stale.getToken()).toBeNull();
    expect(fresh.getToken()).toBe(FRESH);
    expect(fresh.getToken()).toBeNull();

    // An in-memory offer (Deployments' Open in Studio) is newer still:
    // neither earlier connection may take its key...
    expect(offerOmegaConnect(SERVER, OFFERED)).toBe(true);
    expect(stale.getToken()).toBeNull();
    expect(fresh.getToken()).toBeNull();
    expect(hasOmegaConnectToken()).toBe(true);
    // ...only the Studio tab opened for it.
    tab.unmount();
    const next = renderHook(() => useStudioConnection(), { wrapper: Router });
    const offered = next.result.current.connection;
    expect(offered).toMatchObject({ source: "link", origin: SERVER });
    expect(offered.getToken()).toBe(OFFERED);
    expect(offered.getToken()).toBeNull();
    expect(hasOmegaConnectToken()).toBe(false);
  });

  it("hands a fresh link's key exactly once, to the new frame, at exactly the new server's origin", async () => {
    planLink();
    renderStudio();
    const stale = frame();
    const stalePosted = spyOnFrame(stale);
    await fromChild(stale, HELLO);
    await fromChild(stale, READY);
    expect(keys(stalePosted)).toEqual([
      { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
    ]);

    await freshLinkWhileStaleFrameSaysHello(stale);

    const fresh = frame();
    expect(fresh).not.toBe(stale);
    expect(fresh).toHaveAttribute("src", `${OTHER}/?embed=1`);
    const freshPosted = spyOnFrame(fresh);
    await fromChild(fresh, HELLO, OTHER);
    // A repeated hello from the same document gets no second key.
    await fromChild(fresh, HELLO, OTHER);

    expect(keys(freshPosted)).toEqual([
      { message: { type: "embed.key", token: FRESH }, targetOrigin: OTHER },
    ]);
    // The old frame got its own key and nothing that carries the new one.
    expect(keys(stalePosted)).toHaveLength(1);
    expect(carrying(stalePosted, FRESH)).toEqual([]);
    expect(hasOmegaConnectToken()).toBe(false);
  });

  it("under StrictMode takes no key while rendering, then hands each link's key only to its own frame", async () => {
    planLink();
    renderStudio({ strict: true });
    // The initializer ran twice and one result was thrown away; the key is
    // still waiting for the kept connection's frame.
    expect(hasOmegaConnectToken()).toBe(true);
    const stale = frame();
    const stalePosted = spyOnFrame(stale);
    await fromChild(stale, HELLO);
    expect(keys(stalePosted)).toEqual([
      { message: { type: "embed.key", token: TOKEN }, targetOrigin: SERVER },
    ]);
    expect(hasOmegaConnectToken()).toBe(false);

    await freshLinkWhileStaleFrameSaysHello(stale);

    const fresh = frame();
    expect(fresh).not.toBe(stale);
    expect(hasOmegaConnectToken()).toBe(true);
    const freshPosted = spyOnFrame(fresh);
    await fromChild(fresh, HELLO, OTHER);

    expect(keys(freshPosted)).toEqual([
      { message: { type: "embed.key", token: FRESH }, targetOrigin: OTHER },
    ]);
    expect(keys(stalePosted)).toHaveLength(1);
    expect(carrying(stalePosted, FRESH)).toEqual([]);
  });
});
