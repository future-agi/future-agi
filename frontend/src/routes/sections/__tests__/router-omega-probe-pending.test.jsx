import React from "react";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { settleOmegaConsoleSwitch } from "src/sections/omega/shell/flags";
import { resetGatewayProbe } from "src/sections/omega/shell/gatewayProbe";
import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";

import Router from "../index";
import {
  omegaDashboardRoutes,
  omegaSettingsRoutes,
  omegaStandaloneRoutes,
} from "../omega";

// The real Router with the real route builders (router-restricted.test.jsx
// stubs the same guards and layout). With VITE_OMEGA_CONSOLE unset, main.jsx
// starts the gateway probe and renders at once. A deep link to an Omega URL
// opened while the probe is out keeps its URL and shows the loading screen,
// never the 404; when the gateway answers, the same URL becomes the Console;
// when the answer is "no gateway", or the switch is "0", the same URL shows
// the not-enabled page, never the 404. Every Omega product is admitted
// (ShellTestProviders' default), so only the switch decides.

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: {
      id: "u1",
      role: "Owner",
      organization_role: "Owner",
      getStartedCompleted: true,
      ws_enabled: false,
    },
  }),
}));
vi.mock("src/auth/guard", async (importOriginal) => {
  const actual = await importOriginal();
  const pass = ({ children }) => children ?? <Outlet />;
  return Object.fromEntries(Object.keys(actual).map((name) => [name, pass]));
});
vi.mock("src/layouts/dashboard", () => ({
  default: ({ children }) => children ?? <Outlet />,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isEE: false,
    isSuccess: true,
    isLoading: false,
  }),
  usePostLoginPath: () => "/dashboard/develop",
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/Mixpanel", () => {
  const names = new Proxy({}, { get: (_, key) => String(key) });
  // getPageViewEvent: the dashboard's page-view effect (routes/sections/
  // dashboard.jsx) runs on a deep link into a dashboard path; null falls back
  // to Events.pageView there.
  return {
    trackEvent: () => {},
    getPageViewEvent: () => null,
    Events: names,
    PropertyName: names,
  };
});
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <div data-testid="workspace-switcher" />,
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

const CONSOLE_URL = "/dashboard/console";
const DEPLOYMENTS_URL = "/dashboard/deployments";
const PROBE_URL = "/omega/api/healthz";
// sections/omega/shell/OmegaNotEnabledPage.jsx (written out, so this file
// loads before that page exists).
const NOT_ENABLED = "Omega Console isn't enabled on this installation";
const NOT_ENABLED_LINE =
  "An admin turns it on by running the Omega gateway with this app, or by setting VITE_OMEGA_CONSOLE to 1.";

function response(status, body, type = "application/json; charset=utf-8") {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name) => (name.toLowerCase() === "content-type" ? type : null),
    },
    json: async () => (typeof body === "string" ? JSON.parse(body) : body),
  };
}

// services/omega-gateway/src/server.mjs: GET /healthz -> 200 {"live": true}.
const gatewayLive = () => response(200, { live: true });
// frontend/nginx.conf `location = /omega/api/healthz`: no gateway.
const uiNoGateway = () => response(200, '{"live":false}');

// The probe's answer comes when the test gives it; any other fetch fails as
// a network error.
function probeFetch() {
  let respond;
  const answered = new Promise((resolve) => {
    respond = resolve;
  });
  const fetchMock = vi.fn((url) =>
    url === PROBE_URL
      ? answered
      : Promise.reject(new TypeError("Failed to fetch")),
  );
  return { fetchMock, respond: (make) => respond(make()) };
}

function CurrentUrl() {
  return <p data-testid="url">{useLocation().pathname}</p>;
}

function renderAt(url) {
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={[url]}>
        <Router />
        <CurrentUrl />
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

describe("an Omega deep link while the gateway probe is out", () => {
  const originalMatchMedia = window.matchMedia;
  beforeEach(() => {
    resetGatewayProbe();
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    vi.stubEnv("VITE_OMEGA_API_BASE", "");
    ensureWebStorage();
    installViewport(1280);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    resetGatewayProbe();
    window.matchMedia = originalMatchMedia;
  });

  it("shows the loading screen, not the 404, then the Console when the gateway answers", async () => {
    const { fetchMock, respond } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    // main.jsx: ask, then render at once.
    const settled = settleOmegaConsoleSwitch();
    renderAt(CONSOLE_URL);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent(CONSOLE_URL);
    // Still held a moment later: nothing has sent it to the catch-all.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByTestId("url")).toHaveTextContent(CONSOLE_URL);
    expect(screen.queryByTestId("omega-console-layout")).not.toBeInTheDocument();

    await act(async () => {
      respond(gatewayLive);
      await settled;
    });
    expect(
      await screen.findByTestId("omega-console-layout", {}, { timeout: 10_000 }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent(CONSOLE_URL);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the not-enabled page, keeping the URL, once the answer is 'no gateway'", async () => {
    const { fetchMock, respond } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const settled = settleOmegaConsoleSwitch();
    renderAt(CONSOLE_URL);
    expect(screen.getByTestId("url")).toHaveTextContent(CONSOLE_URL);
    await act(async () => {
      respond(uiNoGateway);
      await settled;
    });
    expect(
      await screen.findByText(NOT_ENABLED, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText(NOT_ENABLED_LINE)).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent(CONSOLE_URL);
    expect(screen.queryByTestId("omega-console-layout")).not.toBeInTheDocument();
  });

  // Where "/" lands depends on the switch (usePostLoginPath), so it waits.
  // Only the hold is checked here: after the answer it is the usual
  // <Navigate>, and following it would render a whole Future AGI page.
  it("holds '/' while the probe is out", async () => {
    const { fetchMock } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    settleOmegaConsoleSwitch();
    renderAt("/");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.getByTestId("url")).toHaveTextContent(/^\/$/);
  });

  it("shows the not-enabled page at once, asking nothing, with VITE_OMEGA_CONSOLE=0", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "0");
    const { fetchMock } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    await settleOmegaConsoleSwitch();
    renderAt(CONSOLE_URL);
    expect(
      await screen.findByText(NOT_ENABLED, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent(CONSOLE_URL);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// One row per path family (dashboard, settings, standalone) with the switch
// "0" and with it unset and the gateway answering "no gateway": the same paths
// as with the switch on, each the not-enabled page. "1" keeps the real routes.
describe("the not-enabled page", () => {
  const originalMatchMedia = window.matchMedia;
  beforeEach(() => {
    resetGatewayProbe();
    vi.stubEnv("VITE_OMEGA_API_BASE", "");
    ensureWebStorage();
    installViewport(1280);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    resetGatewayProbe();
    window.matchMedia = originalMatchMedia;
  });

  const paths = (routes) => routes.map((route) => route.path);

  async function settleDown() {
    const { fetchMock, respond } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const settled = settleOmegaConsoleSwitch();
    await act(async () => {
      respond(uiNoGateway);
      await settled;
    });
  }

  it.each([
    ["0", async () => vi.stubEnv("VITE_OMEGA_CONSOLE", "0")],
    [
      "unset, gateway down",
      async () => {
        vi.stubEnv("VITE_OMEGA_CONSOLE", "");
        await settleDown();
      },
    ],
  ])("dashboard family (%s): a deep link to /dashboard/deployments draws the page", async (_, setUp) => {
    await setUp();
    renderAt(DEPLOYMENTS_URL);
    expect(
      await screen.findByText(NOT_ENABLED, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("url")).toHaveTextContent(DEPLOYMENTS_URL);
  });

  it.each([
    ["0", async () => vi.stubEnv("VITE_OMEGA_CONSOLE", "0")],
    [
      "unset, gateway down",
      async () => {
        vi.stubEnv("VITE_OMEGA_CONSOLE", "");
        await settleDown();
      },
    ],
  ])("standalone family (%s): a deep link to /dashboard/console draws the page", async (_, setUp) => {
    await setUp();
    renderAt(CONSOLE_URL);
    expect(
      await screen.findByText(NOT_ENABLED, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("omega-console-layout")).not.toBeInTheDocument();
  });

  it.each([
    ["0", async () => vi.stubEnv("VITE_OMEGA_CONSOLE", "0")],
    [
      "unset, gateway down",
      async () => {
        vi.stubEnv("VITE_OMEGA_CONSOLE", "");
        await settleDown();
      },
    ],
  ])("settings family (%s): the same paths as with the switch on, each the page", async (_, setUp) => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    const on = paths(omegaSettingsRoutes());
    vi.stubEnv("VITE_OMEGA_CONSOLE", "");
    await setUp();
    const off = omegaSettingsRoutes();
    expect(paths(off)).toEqual(on);
    for (const route of off) {
      const { unmount } = render(
        <ShellTestProviders>
          <MemoryRouter>{route.element}</MemoryRouter>
        </ShellTestProviders>,
      );
      expect(screen.getByText(NOT_ENABLED)).toBeInTheDocument();
      unmount();
    }
  });

  it("keeps sign-in's landing ('home') unregistered when the switch is off", () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "0");
    expect(paths(omegaDashboardRoutes())).not.toContain("home");
    expect(paths(omegaDashboardRoutes())).toContain("deployments");
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    expect(paths(omegaDashboardRoutes())).toContain("home");
  });

  it("keeps the real routes with VITE_OMEGA_CONSOLE=1, asking nothing", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
    const { fetchMock } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    await settleOmegaConsoleSwitch();
    renderAt(CONSOLE_URL);
    expect(
      await screen.findByTestId("omega-console-layout", {}, { timeout: 10_000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText(NOT_ENABLED)).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(paths(omegaStandaloneRoutes())).toEqual(["/dashboard/console/*"]);
  });
});
