// The Console page's host parts (CP F-HOST): batched keys (P7), telemetry to
// Sentry (P8), the viewer's panel (P10), the Inbox count (P15), the Console
// on its own origin (P6), and the Hub and remix landings.
import React from "react";
import { Buffer } from "node:buffer";
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

import { EMBED_STATUS } from "src/sections/omega/embed";
import {
  readConsoleInbox,
  resetConsoleInboxForTests,
} from "src/sections/omega/shell/console-inbox";
import { resetConsoleAccessForTests } from "src/sections/omega/shell/omega-access";

import ConsoleFramePage from "../ConsoleFramePage";
import {
  AssertionMintError,
  MINT_STATE,
  VIEWER_REFUSAL_CODE,
} from "../session-assertion";

const sentry = vi.hoisted(() => ({
  captureEvent: vi.fn(),
  addBreadcrumb: vi.fn(),
  startInactiveSpan: vi.fn(() => ({ end: () => {} })),
}));
vi.mock("@sentry/react", () => sentry);
vi.mock("../console-chrome-link", () => ({
  useConsoleChrome: () => ({ frameShown: false, setFrameShown: () => {} }),
}));
const levels = vi.hoisted(() => ({ org: null, ws: null }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: levels.org }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ wsLevel: levels.ws, currentWorkspaceId: "ws-a" }),
}));

const ORIGIN = window.location.origin;
// The Console names its document in every hello and gate and uses only a key
// that echoes it (RL 1275A); the Console frame requires it (requireDoc).
const DOC = "AAECAwQFBgcICQoLDA0ODw";
const HELLO = { type: "embed.hello", protocol: "omega-embed/1", doc: DOC };
const keys = (n) => Array.from({ length: n }, (_, i) => `omega_delegate_k${i}`);
const batchMint = () =>
  vi.fn(async ({ count = 1 } = {}) => ({
    token: keys(count)[0],
    tokens: keys(count),
    expiresAt: 0,
  }));

let currentLocation;
function LocationProbe() {
  currentLocation = useLocation();
  return null;
}

const renderPage = ({
  path = "/dashboard/console",
  src,
  mintAssertion,
} = {}) => {
  const mint = mintAssertion || batchMint();
  render(
    <HelmetProvider>
      <ThemeProvider theme={createTheme()}>
        <MemoryRouter initialEntries={[path]}>
          <LocationProbe />
          <Routes>
            <Route
              path="/dashboard/console/*"
              element={<ConsoleFramePage src={src} mintAssertion={mint} />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
  // These are post-load protocol fixtures, not lifecycle-ordering cases.
  const element = screen.queryByTestId("omega-embed-frame");
  if (element) act(() => fireEvent.load(element));
  return mint;
};

const frame = () => screen.getByTestId("omega-embed-frame");
const spyOnFrame = () => {
  const posted = [];
  vi.spyOn(frame().contentWindow, "postMessage").mockImplementation(
    (message, targetOrigin) => posted.push({ message, targetOrigin }),
  );
  return posted;
};
const post = (data, origin = ORIGIN) =>
  act(async () => {
    window.dispatchEvent(
      new MessageEvent("message", {
        data,
        origin,
        source: frame().contentWindow,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

beforeEach(() => {
  Object.assign(levels, { org: null, ws: null });
  Object.values(sentry).forEach((fn) => fn.mockClear?.());
});

afterEach(() => {
  act(() => {
    resetConsoleInboxForTests();
    resetConsoleAccessForTests();
  });
  delete window.__FUTURE_AGI_CONFIG__;
  vi.restoreAllMocks();
});

describe("Console page: batched keys (CP P7)", () => {
  it("answers the Console's opening hello (want 4) with one mint and four keys", async () => {
    const mint = renderPage();
    const posted = spyOnFrame();
    await post({ ...HELLO, build: "r9-2803ad68", want: 4 });
    expect(mint).toHaveBeenCalledTimes(1);
    expect(mint).toHaveBeenCalledWith({ count: 4 });
    expect(posted.slice(0, 4).map((p) => p.message)).toEqual([
      { type: "embed.key", token: keys(4)[0], left: 3, doc: DOC },
      { type: "embed.key", token: keys(4)[1], left: 2, doc: DOC },
      { type: "embed.key", token: keys(4)[2], left: 1, doc: DOC },
      { type: "embed.key", token: keys(4)[3], doc: DOC },
    ]);
    expect(posted.every((p) => p.targetOrigin === ORIGIN)).toBe(true);
  });
});

describe("Console page: health reports and the Inbox (CP P8, P15)", () => {
  it("forwards an error report to Sentry and a badge to the title and the switcher's store", async () => {
    renderPage();
    spyOnFrame();
    await post(HELLO);
    await post({
      type: "embed.telemetry",
      kind: "error",
      name: "render",
      routeTemplate: "/agents/:agentId",
      release: "r9-2803ad68",
      code: "render_error",
      requestId: "req_6f1c2b9e0a7d4c3b8e5f",
      message: "TypeError: x is undefined",
    });
    expect(sentry.captureEvent).toHaveBeenCalledTimes(1);
    const [event] = sentry.captureEvent.mock.calls[0];
    expect(event.tags.product).toBe("omega-console");
    expect(JSON.stringify(event)).not.toContain("TypeError");

    await post({ type: "embed.badge", inbox: 2 });
    expect(readConsoleInbox()).toBe(2);
    await waitFor(() => expect(document.title).toBe("(2) Omega Console"));
    await post({ type: "embed.badge", inbox: 0 });
    await waitFor(() => expect(document.title).toBe("Omega Console"));
  });
});

describe("Console page: viewer (CP P10)", () => {
  it("shows a viewer the panel and creates no frame, so nothing is minted", () => {
    Object.assign(levels, { org: 3, ws: 1 });
    const mint = renderPage();
    expect(screen.getByTestId("omega-console-viewer")).toBeInTheDocument();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
    expect(
      screen.getByText("Viewers can't use Omega Console"),
    ).toBeInTheDocument();
    expect(mint).not.toHaveBeenCalled();
  });

  it("checks again once, and opens the Console when the role has changed", async () => {
    Object.assign(levels, { org: 3, ws: 1 });
    const mint = vi.fn(async () => {
      throw new AssertionMintError({
        state: MINT_STATE.VIEWER,
        status: 403,
        code: VIEWER_REFUSAL_CODE,
      });
    });
    renderPage({ mintAssertion: mint });
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(
      await screen.findByText("You're still a viewer in this workspace."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();

    mint.mockImplementation(async () => ({
      token: keys(1)[0],
      tokens: keys(1),
    }));
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByTestId("omega-embed-frame")).toBeInTheDocument();
    expect(mint).toHaveBeenCalledTimes(2);
  });

  it("reads any 403 on Check again as still not admitted, and only no answer as couldn't check", async () => {
    Object.assign(levels, { org: 1, ws: 1 });
    // What the live stack answers a viewer: the workspace write guard's
    // plain permission_denied, before the exchange's own viewer reason.
    const mint = vi.fn(async () => {
      throw new AssertionMintError({
        state: MINT_STATE.REFUSED,
        status: 403,
        code: "permission_denied",
      });
    });
    renderPage({ mintAssertion: mint });
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(
      await screen.findByText("You're still a viewer in this workspace."),
    ).toBeInTheDocument();

    mint.mockImplementation(async () => {
      throw new AssertionMintError({
        state: MINT_STATE.UNAVAILABLE,
        status: 503,
        code: null,
      });
    });
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));
    expect(
      await screen.findByText(
        "Future AGI couldn't check your access just now. Try again in a moment.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
  });

  it("swaps the frame for the panel when the exchange refuses a viewer", async () => {
    const mint = vi.fn(async () => {
      throw new AssertionMintError({
        state: MINT_STATE.VIEWER,
        status: 403,
        code: VIEWER_REFUSAL_CODE,
      });
    });
    renderPage({ mintAssertion: mint });
    spyOnFrame();
    await post(HELLO);
    expect(
      await screen.findByTestId("omega-console-viewer"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("omega-embed-frame")).toBeNull();
  });
});

describe("Console page: the Console on its own origin (CP P6)", () => {
  const CONSOLE = "http://127.0.0.1:43211";

  it("sandboxes the frame and speaks only with the Console origin", async () => {
    renderPage({ src: `${CONSOLE}/omega/console/?embed=1` });
    const element = frame();
    expect(element.getAttribute("sandbox")).toContain("allow-same-origin");
    expect(element.getAttribute("sandbox")).not.toContain(
      "allow-top-navigation",
    );
    expect(element.getAttribute("allow")).toContain("clipboard-write");
    expect(screen.getByTestId("omega-tab-console").dataset.frameOrigin).toBe(
      "console",
    );
    const posted = spyOnFrame();
    // A hello in the right window from this page's own origin is not the Console.
    await post(HELLO, ORIGIN);
    expect(posted).toEqual([]);
    await post(HELLO, CONSOLE);
    expect(posted[0].targetOrigin).toBe(CONSOLE);
    // Only the Console's own word, from its own origin, confirms the key.
    await post({ type: "embed.gate", state: "ready", doc: DOC }, ORIGIN);
    expect(screen.getByTestId("omega-tab-console").dataset.state).toBe(
      EMBED_STATUS.checking,
    );
    await post({ type: "embed.gate", state: "ready", doc: DOC }, CONSOLE);
    expect(screen.getByTestId("omega-tab-console").dataset.state).toBe(
      EMBED_STATUS.connected,
    );
  });

  it("reads the Console origin from the runtime config", () => {
    window.__FUTURE_AGI_CONFIG__ = {
      VITE_OMEGA_CONSOLE_URL: `${CONSOLE}/omega/console/?embed=1`,
    };
    renderPage();
    expect(frame().getAttribute("src")).toBe(
      `${CONSOLE}/omega/console/?embed=1`,
    );
  });
});

describe("Console page: landings", () => {
  const TOKEN = `rpl_v1.${Buffer.from('{"c":"c1","s":"s1","exp":1}').toString(
    "base64url",
  )}.${"B".repeat(43)}`;

  it("opens a Hub template's import route when the handoff is on", async () => {
    window.__FUTURE_AGI_CONFIG__ = { VITE_OMEGA_HUB_CONSOLE: "1" };
    renderPage({
      path: "/dashboard/console?hub=futureagi%2Fsupport-triage%401.2.0",
    });
    await waitFor(() =>
      expect(currentLocation.pathname).toBe(
        "/dashboard/console/templates/import",
      ),
    );
    expect(frame().getAttribute("src")).toBe(
      `${ORIGIN}/omega/console/?embed=1#/templates/import?hub=futureagi%2Fsupport-triage%401.2.0`,
    );
  });

  it("drops the Hub reference when the handoff is off", async () => {
    renderPage({
      path: "/dashboard/console?hub=futureagi%2Fsupport-triage%401.2.0",
    });
    await waitFor(() => expect(currentLocation.search).toBe(""));
    expect(frame().getAttribute("src")).toBe(
      `${ORIGIN}/omega/console/?embed=1`,
    );
  });

  it("hands a remix link to the Console only when remix is on", async () => {
    window.__FUTURE_AGI_CONFIG__ = { VITE_OMEGA_CONSOLE_REMIX: "true" };
    const first = renderPage({ path: `/dashboard/console/remix/${TOKEN}` });
    expect(first).toBeDefined();
    expect(frame().getAttribute("src")).toBe(
      `${ORIGIN}/omega/console/?embed=1#/remix/${TOKEN}`,
    );
  });

  it("lands a remix link on the Console home while remix is off", async () => {
    renderPage({ path: `/dashboard/console/remix/${TOKEN}` });
    await waitFor(() =>
      expect(currentLocation.pathname).toBe("/dashboard/console"),
    );
    expect(frame().getAttribute("src")).not.toContain("rpl_v1");
  });
});
