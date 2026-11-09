/* eslint-env node */
// OWNER-RULINGS 28 (OD-21): no deployment access token in any URL, fragment
// included. These tests fail the moment a token shows up in an href, a src,
// the address bar or a navigation, on every Deployments surface that can hold
// one (the list, each detail tab and the wizard's result), for a deployment
// only this host can reach and for one with a network address.
import fs from "node:fs";
import path from "node:path";
import React from "react";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  HANDOFF_TTL_MS,
  offerOmegaConnect,
  peekOmegaConnect,
  resetOmegaConnectForTests,
  takeOmegaConnectToken,
} from "src/sections/omega/embed/connect-intake";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";

import {
  keepAccessToken,
  resetAccessTokensForTests,
} from "../lib/accessTokens";
import {
  ID,
  detailView,
  fakeApi,
  renderDeployments,
  summaryOf,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));

const { default: DeploymentsListPage } = await import("../DeploymentsListPage");
const { default: DeploymentDetailPage } = await import(
  "../DeploymentDetailPage"
);
const { DeployTracker } = await import("../new/DeployStep");

// Distinctive, and long enough that a partial copy is still caught.
const TOKEN = "dtok_Zq9X-4hV2mWc8LrT1yN6pB3sKfJ0aE7uGdHi5oQx";

const NETWORK = {
  url: "https://greeter.agents.example.com",
  studioUrl: "https://greeter.agents.example.com/",
  urlReach: "network",
  urlNote: null,
};

const REACHES = [
  ["only this host can reach", {}],
  ["has a network address", NETWORK],
];

/** Every URL-bearing attribute on the page, the router's location and the address bar. */
function everyUrl() {
  const attributes = [
    ...document.querySelectorAll("[href], [src], [action], [formaction]"),
  ].flatMap((el) =>
    ["href", "src", "action", "formaction"]
      .map((name) => el.getAttribute(name))
      .filter((value) => value !== null),
  );
  const probe = screen.queryByTestId("location")?.textContent ?? "";
  return [...attributes, probe, window.location.href];
}

/** A URL that carries a token: the token itself, or any t= / token= in its query or fragment. */
function leaks(url) {
  if (url.includes(TOKEN)) return true;
  return /[?#&](?:t|token|access_token|accessToken)=[^&#]+/u.test(url);
}

function expectNoTokenInAnyUrl(where) {
  const urls = everyUrl();
  expect(urls.length, where).toBeGreaterThan(0);
  expect(urls.filter(leaks), where).toEqual([]);
}

beforeEach(() => {
  Object.assign(session, { state: "ready", code: null, message: null });
  resetAccessTokensForTests();
  resetOmegaConnectForTests();
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
});
// Only this file's own spies are restored (in the test that makes them): a
// global restoreAllMocks would also reset the setup file's matchMedia mock.
afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("no access token in any URL (OWNER-RULINGS 28, OD-21)", () => {
  it.each(REACHES)(
    "the list, while this tab holds the token of a deployment that %s",
    async (_name, reach) => {
      keepAccessToken({ id: ID, accessToken: TOKEN });
      const api = fakeApi({
        list: vi.fn(async () => [summaryOf(detailView(reach))]),
      });
      renderDeployments(<DeploymentsListPage />, { api });
      await screen.findAllByTestId("deployment-row");
      expectNoTokenInAnyUrl("list");
    },
  );

  it.each(REACHES)(
    "every detail tab, with Open in Studio on, for a deployment that %s",
    async (_name, reach) => {
      keepAccessToken({ id: ID, accessToken: TOKEN });
      const api = fakeApi({ get: vi.fn(async () => detailView(reach)) });
      for (const tab of ["", "/revisions", "/logs", "/settings"]) {
        const view = renderDeployments(<DeploymentDetailPage />, {
          api,
          route: `/dashboard/deployments/${ID}${tab}`,
          parts: [OMEGA_PART.DEPLOYMENT_STUDIO],
        });
        await screen.findByRole("heading", { level: 1 });
        await waitFor(() =>
          expect(document.querySelectorAll("[href]").length).toBeGreaterThan(0),
        );
        expectNoTokenInAnyUrl(`detail ${tab || "/overview"}`);
        view.unmount();
      }
    },
  );

  it("the Studio page link stays bare once the token is held, and Open in Studio moves without it", async () => {
    // A network deployment: a host-only one links no Studio page (DEMO-POLISH-3).
    renderDeployments(<DeploymentDetailPage />, {
      api: fakeApi({ get: vi.fn(async () => detailView(NETWORK)) }),
      route: `/dashboard/deployments/${ID}`,
      parts: [OMEGA_PART.DEPLOYMENT_STUDIO],
    });
    const link = await screen.findByTestId("open-studio-page");
    const bare = link.getAttribute("href");
    keepAccessToken({ id: ID, accessToken: TOKEN });
    await waitFor(() =>
      expect(screen.getByTestId("open-in-studio")).toBeEnabled(),
    );
    expect(screen.getByTestId("open-studio-page").getAttribute("href")).toBe(
      bare,
    );
    expectNoTokenInAnyUrl("detail with the token");
    const pushState = vi.spyOn(window.history, "pushState");
    const replaceState = vi.spyOn(window.history, "replaceState");
    await userEvent.click(screen.getByTestId("open-in-studio"));
    expect(screen.getByTestId("location")).toHaveTextContent(
      /^\/dashboard\/studio$/u,
    );
    expectNoTokenInAnyUrl("after Open in Studio");
    const calls = [...pushState.mock.calls, ...replaceState.mock.calls];
    pushState.mockRestore();
    replaceState.mockRestore();
    for (const call of calls) {
      expect(JSON.stringify(call)).not.toContain(TOKEN);
    }
    expect(takeOmegaConnectToken()).toBe(TOKEN);
  });

  it.each(REACHES)(
    "the wizard's result, with the token this deploy just minted, for a deployment that %s",
    async (_name, reach) => {
      keepAccessToken({ id: ID, accessToken: TOKEN });
      const api = fakeApi({ get: vi.fn(async () => detailView(reach)) });
      renderDeployments(
        <DeployTracker id={ID} revision={2} onEdit={() => {}} />,
        { api, route: "/dashboard/deployments/new" },
      );
      await screen.findByTestId("deploy-result");
      expect(screen.getByTestId("access-token-notice")).toBeInTheDocument();
      // Beside the token it shows, the result links the Studio page for
      // either reach, bare (DEMO-POLISH-3 keeps a host-only one off the
      // detail page only).
      expect(screen.getByTestId("open-studio-page")).toBeInTheDocument();
      expectNoTokenInAnyUrl("wizard result");
    },
  );
});

describe("the in-memory hand-off to the Studio tab", () => {
  const SERVER = "http://127.0.0.1:55408";

  it("is single-use and holds the token no longer than 60 seconds", () => {
    expect(HANDOFF_TTL_MS).toBeLessThanOrEqual(60_000);
    const now = Date.now();
    vi.useFakeTimers({ now });
    expect(offerOmegaConnect(SERVER, TOKEN)).toBe(true);
    vi.setSystemTime(now + HANDOFF_TTL_MS);
    expect(takeOmegaConnectToken()).toBe(TOKEN);
    expect(takeOmegaConnectToken()).toBeNull();

    expect(offerOmegaConnect(SERVER, TOKEN)).toBe(true);
    vi.setSystemTime(now + 2 * HANDOFF_TTL_MS + 1);
    // Expired: dropped whole, so a later visit to the Studio tab connects to nothing.
    expect(peekOmegaConnect()).toBeNull();
    expect(takeOmegaConnectToken()).toBeNull();
  });

  it("is honoured only on the origin that made it", () => {
    expect(
      offerOmegaConnect(SERVER, TOKEN, { origin: "https://other.example" }),
    ).toBe(true);
    expect(peekOmegaConnect()).toBeNull();
    expect(takeOmegaConnectToken()).toBeNull();
  });

  it("refuses what the link intake refuses, and holds nothing then", () => {
    expect(offerOmegaConnect("ftp://127.0.0.1:55408", TOKEN)).toBe(false);
    expect(offerOmegaConnect(SERVER, "")).toBe(false);
    expect(offerOmegaConnect(SERVER, "x".repeat(4097))).toBe(false);
    expect(peekOmegaConnect()).toBeNull();
  });
});

describe("no Deployments source builds a URL with a token in it", () => {
  const ROOT = path.resolve(__dirname, "..");
  const sources = (dir) =>
    fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        return entry.name === "__tests__" ? [] : sources(full);
      }
      return /\.(?:js|jsx|ts|tsx)$/u.test(entry.name) ? [full] : [];
    });
  // A template or a concatenation that puts a value after t= / token= in a
  // query or fragment: `#t=${token}`, "?token=" + token, and the like.
  const BUILDS_TOKEN_URL =
    /[?#&](?:t|token|access_token|accessToken)=(?:\$\{|["'`]\s*\+)/u;

  it("finds the sources it scans", () => {
    const files = sources(ROOT).map((file) => path.relative(ROOT, file));
    expect(files).toEqual(
      expect.arrayContaining([
        path.join("lib", "links.js"),
        path.join("lib", "reach.js"),
        path.join("components", "OpenInStudio.jsx"),
      ]),
    );
  });

  it("finds no token put into a URL", () => {
    const offenders = sources(ROOT).filter((file) =>
      BUILDS_TOKEN_URL.test(fs.readFileSync(file, "utf8")),
    );
    expect(offenders.map((file) => path.relative(ROOT, file))).toEqual([]);
  });

  it("would catch the shapes it exists for", () => {
    for (const shape of [
      "`${url}#t=${encodeURIComponent(token)}`",
      "`/dashboard/studio#connect=${u}&t=${t}`",
      "url + '?token=' + token",
      'base + "&access_token=" + value',
    ]) {
      expect(BUILDS_TOKEN_URL.test(shape), shape).toBe(true);
    }
  });
});
