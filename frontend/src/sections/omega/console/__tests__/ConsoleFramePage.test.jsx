import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { HelmetProvider } from "react-helmet-async";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMBED_STATUS, NO_KEY_GATE_TIMEOUT_MS } from "src/sections/omega/embed";

import ConsoleFramePage from "../ConsoleFramePage";
import { AssertionMintError, MINT_STATE } from "../session-assertion";

const chrome = vi.hoisted(() => ({ setFrameShown: null }));
vi.mock("../console-chrome-link", () => ({
  useConsoleChrome: () => ({
    frameShown: false,
    setFrameShown: chrome.setFrameShown,
  }),
}));

const ORIGIN = window.location.origin;
// The Console names its document in every hello and gate and uses only a key
// that echoes it; the Console frame requires it (requireDoc).
const DOC = "AAECAwQFBgcICQoLDA0ODw";
const HELLO = { type: "embed.hello", protocol: "omega-embed/1", doc: DOC };
// Reconnect mounts a new frame, so a new document with a doc of its own.
const NEW_DOC = "m4gLqkrE04O3BMKxGKKPFg";
const NEW_HELLO = { ...HELLO, doc: NEW_DOC };
const TOKEN = "omega_delegate_eyJ.console.sig";

let currentLocation;
function LocationProbe() {
  currentLocation = useLocation();
  return null;
}

const renderPage = ({
  path = "/dashboard/console",
  mode = "light",
  mintAssertion = vi.fn(async () => ({ token: TOKEN, expiresAt: 0 })),
} = {}) => {
  const view = render(
    <HelmetProvider>
      <ThemeProvider theme={createTheme({ palette: { mode } })}>
        <MemoryRouter initialEntries={[path]}>
          <LocationProbe />
          <Routes>
            <Route
              path="/dashboard/console/*"
              element={<ConsoleFramePage mintAssertion={mintAssertion} />}
            />
            <Route path="*" element={<div data-testid="future-agi-page" />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
  loadFrameOnce();
  return { ...view, mintAssertion };
};

const frame = () => screen.getByTestId("omega-embed-frame");
// Normal Console fixtures start after load; lifecycle edge cases live in
// ConsoleFrame.doc.test.jsx. Reconnect creates a new element and generation.
const loadedFrames = new WeakSet();
const loadFrameOnce = () => {
  const element = frame();
  if (loadedFrames.has(element)) return;
  loadedFrames.add(element);
  act(() => fireEvent.load(element));
};
const pageState = () => screen.getByTestId("omega-tab-console").dataset.state;

const spyOnFrame = () => {
  loadFrameOnce();
  const posted = [];
  vi.spyOn(frame().contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};

const post = (data, { origin = ORIGIN, source } = {}) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin,
        source: source === undefined ? frame().contentWindow : source,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

// A mint failure is known the moment the mint returns, so the page shows its
// reason at once: well inside this bound, and long before the Console's own
// 6 s wait for a key or the shared surface's 8 s fallback.
const SHOWN_AT_ONCE_MS = 500;

const expectShownAtOnce = async (title) => {
  await waitFor(
    () => {
      expect(pageState()).toBe(EMBED_STATUS.needsKey);
      expect(screen.getByText(title)).toBeInTheDocument();
    },
    { timeout: SHOWN_AT_ONCE_MS },
  );
};

const connect = async () => {
  const posted = spyOnFrame();
  await post(HELLO);
  await post({ type: "embed.gate", state: "ready", doc: DOC });
  return posted;
};

beforeEach(() => {
  chrome.setFrameShown = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("ConsoleFramePage", () => {
  it("frames the EE Console in embed mode at the parent's sub-route, with no token in the URL", () => {
    renderPage({ path: "/dashboard/console/agents/a1?tab=runs" });
    const element = frame();

    expect(element.getAttribute("src")).toBe(
      `${ORIGIN}/omega/console/?embed=1#/agents/a1?tab=runs`,
    );
    expect(element.hasAttribute("sandbox")).toBe(false);
    expect(element.getAttribute("title")).toBe("Omega Console");
    expect(pageState()).toBe(EMBED_STATUS.connecting);
    expect(screen.getByText("Opening Omega Console…")).toBeInTheDocument();
  });

  it("hands the frame a freshly minted assertion as its key, then the theme", async () => {
    const { mintAssertion } = renderPage({ mode: "dark" });
    const posted = await connect();

    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(posted.slice(0, 2)).toEqual([
      {
        message: { type: "embed.key", token: TOKEN, doc: DOC },
        targetOrigin: ORIGIN,
      },
      { message: { type: "embed.theme", mode: "dark" }, targetOrigin: ORIGIN },
    ]);
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(frame().getAttribute("src")).not.toContain(TOKEN);
    expect(screen.queryByTestId("omega-embed-gate")).toBeNull();
    // Once the Console confirms its key, its own chrome takes over.
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);
  });

  it("keeps /dashboard/console/<path> and the Console route in step", async () => {
    renderPage();
    const posted = await connect();

    await post({ type: "embed.route", path: "/chat" });
    expect(currentLocation.pathname).toBe("/dashboard/console/chat");

    await post({ type: "embed.route", path: "/skills/sk1?version=2" });
    expect(`${currentLocation.pathname}${currentLocation.search}`).toBe(
      "/dashboard/console/skills/sk1?version=2",
    );
    expect(posted.map((entry) => entry.message.type)).not.toContain(
      "embed.navigate",
    );
  });

  it("brings Future AGI back when the Console's 'Future AGI' entry asks", async () => {
    renderPage({ path: "/dashboard/console/chat" });
    await connect();

    await post({ type: "embed.navigate", path: "/dashboard/observe" });

    expect(currentLocation.pathname).toBe("/dashboard/observe");
    expect(screen.getByTestId("future-agi-page")).toBeInTheDocument();
    // Leaving the page hands the chrome back to the shell.
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
  });

  it("ignores navigate-back requests that are not from the keyed frame", async () => {
    renderPage({ path: "/dashboard/console/chat" });

    // Before the handshake the frame has no say over Future AGI.
    await post({ type: "embed.navigate", path: "/dashboard" });
    expect(currentLocation.pathname).toBe("/dashboard/console/chat");

    await connect();
    await post(
      { type: "embed.navigate", path: "/dashboard" },
      { origin: "http://127.0.0.1:4192" },
    );
    await post(
      { type: "embed.navigate", path: "/dashboard" },
      { source: window },
    );
    await post({ type: "embed.navigate", path: "//evil.example/x" });
    await post({ type: "embed.navigate", path: "https://evil.example/x" });
    await post({ type: "embed.navigate", path: "/dash\\board" });
    await post({ type: "embed.navigate" });

    expect(currentLocation.pathname).toBe("/dashboard/console/chat");
    expect(screen.queryByTestId("future-agi-page")).toBeNull();
  });

  it("explains a refused mint calmly, asks the backend once, and mints again on Reconnect", async () => {
    const mintAssertion = vi.fn(async () => {
      throw new AssertionMintError({ state: MINT_STATE.REFUSED, status: 403 });
    });
    renderPage({ mintAssertion });
    let posted = spyOnFrame();

    await post(HELLO);
    // The Console repeats its hello until it gets a key.
    await post(HELLO);
    await post(HELLO);

    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(posted.map((entry) => entry.message.type)).not.toContain(
      "embed.key",
    );
    await expectShownAtOnce("Omega Console isn't available for your role");
    expect(screen.queryByRole("button", { name: /directly/i })).toBeNull();
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);

    mintAssertion.mockImplementation(async () => ({ token: TOKEN }));
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    posted = spyOnFrame();
    await post(NEW_HELLO);

    expect(mintAssertion).toHaveBeenCalledTimes(2);
    expect(posted[0]).toEqual({
      message: { type: "embed.key", token: TOKEN, doc: NEW_DOC },
      targetOrigin: ORIGIN,
    });
    // The new key is only being checked until the Console confirms it.
    expect(pageState()).toBe(EMBED_STATUS.checking);
    await post({ type: "embed.gate", state: "ready", doc: NEW_DOC });
    expect(pageState()).toBe(EMBED_STATUS.connected);
  });

  it.each([
    [MINT_STATE.SIGNED_OUT, "Your Future AGI session has ended"],
    [MINT_STATE.UNAVAILABLE, "Omega Console can't sign you in right now"],
  ])("names the %s mint failure", async (state, title) => {
    renderPage({
      mintAssertion: vi.fn(async () => {
        throw new AssertionMintError({ state });
      }),
    });
    spyOnFrame();
    await post(HELLO);
    await expectShownAtOnce(title);
  });

  it("keeps the reason when the Console's first hello comes after the no-answer timeout", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const mintAssertion = vi.fn(async () => {
      throw new AssertionMintError({ state: MINT_STATE.REFUSED, status: 403 });
    });
    renderPage({ mintAssertion });
    spyOnFrame();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(7_000);
    });
    await waitFor(() => expect(pageState()).toBe(EMBED_STATUS.timeout));

    // A slow Console wakes up and says hello; the mint is refused. The frame
    // goes back to connecting on its own, which is not a Reconnect.
    await post(HELLO);
    await waitFor(() => expect(pageState()).toBe(EMBED_STATUS.needsKey));
    expect(
      screen.getByText("Omega Console isn't available for your role"),
    ).toBeInTheDocument();

    await post(HELLO);
    await post(HELLO);
    expect(mintAssertion).toHaveBeenCalledTimes(1);
    expect(pageState()).toBe(EMBED_STATUS.needsKey);
  });

  it("shows the session-ended state when the Console refuses its key", async () => {
    renderPage();
    await connect();
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);

    await post({ type: "embed.gate", state: "refused", doc: DOC });

    expect(pageState()).toBe(EMBED_STATUS.refused);
    expect(
      screen.getByText("Your Omega Console session ended"),
    ).toBeInTheDocument();
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(false);
  });

  it("says the Console is unavailable when it never answers", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPage();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(7_000);
    });

    await waitFor(() => expect(pageState()).toBe(EMBED_STATUS.timeout));
    expect(
      screen.getByText("Omega Console isn't available right now"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /directly/i })).toBeNull();
  });
});

// Handing the key over proves nothing on its own: the Console may turn it
// down, or never answer. Only its gate saying "ready" (embed.gate) confirms
// the handshake, so nothing shows connected before that.
describe("ConsoleFramePage handshake", () => {
  const visibility = () => window.getComputedStyle(frame()).visibility;
  const expectNeverShown = () =>
    expect(chrome.setFrameShown).not.toHaveBeenCalledWith(true);

  it("stays pending after handing over the key, and is connected only once the Console confirms it", async () => {
    renderPage();
    const posted = spyOnFrame();
    await post(HELLO);

    expect(posted[0].message.type).toBe("embed.key");
    expect(pageState()).toBe(EMBED_STATUS.checking);
    expect(screen.getByTestId("omega-embed-gate")).toHaveTextContent(
      "Opening Omega Console…",
    );
    expect(visibility()).toBe("hidden");
    expectNeverShown();

    await post({ type: "embed.gate", state: "ready", doc: DOC });

    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(screen.queryByTestId("omega-embed-gate")).toBeNull();
    expect(visibility()).toBe("visible");
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);
  });

  it("asks to reconnect when the Console never confirms its key, and never shows connected", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPage();
    spyOnFrame();
    await post(HELLO);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NO_KEY_GATE_TIMEOUT_MS - 1000);
    });
    expect(pageState()).toBe(EMBED_STATUS.checking);
    expect(screen.getByText("Opening Omega Console…")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await waitFor(() => expect(pageState()).toBe(EMBED_STATUS.needsKey));
    expect(
      screen.getByText("Omega Console needs to reconnect"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();
    expect(visibility()).toBe("hidden");
    expectNeverShown();
  });

  it("shows the session-ended state when the Console refuses its key before confirming it", async () => {
    renderPage();
    spyOnFrame();
    await post(HELLO);
    expect(pageState()).toBe(EMBED_STATUS.checking);

    await post({ type: "embed.gate", state: "refused", doc: DOC });

    expect(pageState()).toBe(EMBED_STATUS.refused);
    expect(
      screen.getByText("Your Omega Console session ended"),
    ).toBeInTheDocument();
    expect(visibility()).toBe("hidden");
    expectNeverShown();
  });

  it("gives the Console no say over Future AGI until it confirms its key", async () => {
    renderPage({ path: "/dashboard/console/chat" });
    spyOnFrame();
    await post(HELLO);

    await post({ type: "embed.navigate", path: "/dashboard" });
    expect(currentLocation.pathname).toBe("/dashboard/console/chat");

    await post({ type: "embed.gate", state: "ready", doc: DOC });
    await post({ type: "embed.navigate", path: "/dashboard" });
    expect(currentLocation.pathname).toBe("/dashboard");
  });

  it("asks to reconnect once the bound passes although the Console keeps saying hello and gets a fresh key each time, and stays there until Reconnect", async () => {
    // A Console that says hello every 500 ms but never sends embed.gate. The
    // minter answers every hello with a fresh key, so every hello is a key
    // handed over; none of them confirms anything.
    const HELLO_EVERY_MS = 500;
    vi.useFakeTimers();
    const { mintAssertion } = renderPage();
    let posted = spyOnFrame();
    const keysHandedOver = () =>
      posted.filter((entry) => entry.message.type === "embed.key").length;
    // Pure fake timers: advanceTimersByTimeAsync lets each hello's mint
    // finish before any timer fires.
    const fromConsole = (data, advanceMs = 0) =>
      act(async () => {
        window.dispatchEvent(
          new MessageEvent("message", {
            data,
            origin: ORIGIN,
            source: frame().contentWindow,
          }),
        );
        await vi.advanceTimersByTimeAsync(advanceMs);
      });
    // Says hello every HELLO_EVERY_MS for `ms`; returns the state shown after
    // each hello.
    const helloFor = async (ms) => {
      const states = [];
      for (let elapsed = 0; elapsed < ms; elapsed += HELLO_EVERY_MS) {
        await fromConsole(HELLO, HELLO_EVERY_MS);
        states.push(pageState());
      }
      return states;
    };

    // Hellos at 0 .. 7000 ms; the clock is at 7500 ms.
    const before = await helloFor(NO_KEY_GATE_TIMEOUT_MS - HELLO_EVERY_MS);
    expect(new Set(before)).toEqual(new Set([EMBED_STATUS.checking]));
    expect(screen.getByText("Opening Omega Console…")).toBeInTheDocument();

    // The bound runs from the first key: it passes at 8000 ms, however many
    // keys came after it.
    await helloFor(2 * HELLO_EVERY_MS);
    expect(pageState()).toBe(EMBED_STATUS.needsKey);
    expect(
      screen.getByText("Omega Console needs to reconnect"),
    ).toBeInTheDocument();

    // More hellos, each handed another key, never bring "pending" back.
    const after = await helloFor(NO_KEY_GATE_TIMEOUT_MS);
    expect(new Set(after)).toEqual(new Set([EMBED_STATUS.needsKey]));
    expect(
      screen.getByRole("button", { name: "Reconnect" }),
    ).toBeInTheDocument();

    // Every hello really was answered with a fresh key.
    const hellos = before.length + 2 + after.length;
    expect(mintAssertion).toHaveBeenCalledTimes(hellos);
    expect(keysHandedOver()).toBe(hellos);
    expect(window.getComputedStyle(frame()).visibility).toBe("hidden");
    expectNeverShown();

    // Reconnect starts a fresh attempt, which the Console can confirm.
    fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    expect(pageState()).toBe(EMBED_STATUS.connecting);
    posted = spyOnFrame();
    await fromConsole(NEW_HELLO);
    expect(keysHandedOver()).toBe(1);
    expect(pageState()).toBe(EMBED_STATUS.checking);
    await fromConsole({ type: "embed.gate", state: "ready", doc: NEW_DOC });
    expect(pageState()).toBe(EMBED_STATUS.connected);
    expect(chrome.setFrameShown).toHaveBeenLastCalledWith(true);
  });
});
