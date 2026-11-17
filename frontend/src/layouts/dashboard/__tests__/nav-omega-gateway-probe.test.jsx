import React, { Suspense } from "react";
import { MemoryRouter, Outlet, useRoutes } from "react-router-dom";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { settleOmegaConsoleSwitch } from "src/sections/omega/shell/flags";
import { resetGatewayProbe } from "src/sections/omega/shell/gatewayProbe";
import {
  ShellTestProviders,
  ensureWebStorage,
  installViewport,
} from "src/sections/omega/shell/__tests__/test-providers";

import NavVertical from "../nav-vertical";

// With VITE_OMEGA_CONSOLE unset the Omega tabs follow /omega/api. The app
// renders at once (main.jsx does not wait for the probe); the tabs appear
// when the gateway answers its /healthz, in the same page, with no reload.
// They stay hidden where the answer is "no gateway", and are never asked
// for with VITE_OMEGA_CONSOLE=0. Every Omega product is admitted
// (ShellTestProviders' default), so only the switch decides.

vi.mock("src/auth/guard", () => ({
  AuthGuard: ({ children }) => children,
}));
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
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    isOSS: false,
    isCloud: true,
    isEE: false,
    isLoading: false,
  }),
}));
vi.mock("src/api/workspaces/list", () => ({
  useWorkspacesList: () => ({ data: [] }),
  useWorkspaceFromList: () => ({ workspace: null }),
}));
vi.mock("src/utils/Mixpanel", () => {
  const names = new Proxy({}, { get: (_, key) => String(key) });
  return { trackEvent: () => {}, Events: names, PropertyName: names };
});
vi.mock("src/layouts/dashboard/WorkspaceSwitcher/WorkspaceSwitcher", () => ({
  default: () => <div data-testid="workspace-switcher" />,
}));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ themeLayout: "vertical", onUpdate: () => {} }),
}));

const AGENT_INFRASTRUCTURE = "Agent infrastructure";
const PROBE_URL = "/omega/api/healthz";

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
// frontend/vercel.json rewrites every path to the SPA.
const spaFallback = () =>
  response(200, '<!doctype html><div id="root"></div>', "text/html");

// The probe's answer comes when the test gives it; any other fetch the shell
// makes fails as a network error, as in the other shell tests.
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

function App() {
  return useRoutes([
    {
      path: "/dashboard",
      element: (
        <div style={{ display: "flex" }}>
          <NavVertical openNav={false} onCloseNav={() => {}} />
          <Suspense fallback={null}>
            <Outlet />
          </Suspense>
        </div>
      ),
      children: [
        { path: "develop", element: <div data-testid="host-develop" /> },
      ],
    },
  ]);
}

function renderNav() {
  render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={["/dashboard/develop"]}>
        <App />
      </MemoryRouter>
    </ShellTestProviders>,
  );
  expect(screen.getByTestId("host-develop")).toBeInTheDocument();
}

// Every group heading of the vertical nav (nav-omega.test.jsx reads the same
// .MuiListSubheader-root). "Build" is there with or without Omega, so the nav
// is known to have rendered before an absence is asserted.
function sidebarSubheaders() {
  const names = Array.from(
    document.querySelectorAll("#nav-section-vertical .MuiListSubheader-root"),
  ).map((heading) => heading.textContent);
  expect(names).toContain("Build");
  return names;
}

describe("Omega tabs with VITE_OMEGA_CONSOLE unset follow /omega/api", () => {
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

  it("renders at once without the tabs, then shows them when the gateway answers, without a reload", async () => {
    const { fetchMock, respond } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    // main.jsx: ask, then render at once.
    const settled = settleOmegaConsoleSwitch();
    renderNav();
    expect(sidebarSubheaders()).not.toContain(AGENT_INFRASTRUCTURE);
    await act(async () => {
      respond(gatewayLive);
      await settled;
    });
    expect(
      await screen.findByText(AGENT_INFRASTRUCTURE, {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      PROBE_URL,
      expect.objectContaining({ method: "GET", credentials: "omit" }),
    );
  });

  it.each([
    ["the UI's 'no gateway' answer", uiNoGateway],
    ["an SPA fallback's 200 index.html", spaFallback],
  ])("keeps the tabs hidden after %s", async (_name, make) => {
    const { fetchMock, respond } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    const settled = settleOmegaConsoleSwitch();
    renderNav();
    await act(async () => {
      respond(make);
      await settled;
    });
    expect(sidebarSubheaders()).not.toContain(AGENT_INFRASTRUCTURE);
    expect(screen.queryByText(AGENT_INFRASTRUCTURE)).not.toBeInTheDocument();
  });

  it("keeps them hidden, and asks nothing, with VITE_OMEGA_CONSOLE=0", async () => {
    vi.stubEnv("VITE_OMEGA_CONSOLE", "0");
    const { fetchMock } = probeFetch();
    vi.stubGlobal("fetch", fetchMock);
    await settleOmegaConsoleSwitch();
    renderNav();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sidebarSubheaders()).not.toContain(AGENT_INFRASTRUCTURE);
  });
});
