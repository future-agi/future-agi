import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import official from "../__fixtures__/entry-official.json";
import meUser from "../__fixtures__/me-user.json";
import rules from "../__fixtures__/rules.json";
import HubBrowsePage from "../HubBrowsePage";
import HubEntryPage from "../HubEntryPage";
import HubPublishPage from "../HubPublishPage";
import TokenSettings from "../components/TokenSettings";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

const config = vi.hoisted(() => ({
  apiBase: "https://registry.example.test/api/",
}));
vi.mock("src/config-global", () => ({
  get HOST_API() {
    return config.apiBase;
  },
}));
vi.mock("src/utils/axios", () => import("./mockAxios"));

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const REGISTRY = "https://registry.example.test/api/omega-hub/v1";
const CATALOGUE = "https://docs.futureagi.com/hub/";
const OLD_VERSION = "0.9.0";

beforeEach(() => {
  config.apiBase = "https://registry.example.test/api/";
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
});
afterEach(() => vi.unstubAllEnvs());

function entryRoutes(parts = []) {
  const entry = structuredClone(official);
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", ENTRY, entry],
    [
      "get",
      `${ENTRY}/versions`,
      [entry.latest, { ...entry.latest, version: OLD_VERSION }],
    ],
    [
      "get",
      `${ENTRY}/versions/${OLD_VERSION}`,
      { ...entry.latest, version: OLD_VERSION },
    ],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/rules", rules],
  ]);
}

const entryPage = (query = "") =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at: `/dashboard/hub/future-agi/first-agent${query}`,
  });

// D4a is independent of hub_cli. These tests must not turn on native handoffs.
describe("In-app version downloads", () => {
  it.each(["", `?version=${OLD_VERSION}`])(
    "downloads the resolved version with no CLI or native handoff (%s)",
    async (query) => {
      entryRoutes();
      entryPage(query);
      const version = query ? OLD_VERSION : official.latest.version;
      const link = await screen.findByRole("link", {
        name: "Download (.tar.gz)",
      });
      expect(link).toHaveAttribute(
        "href",
        `${REGISTRY}/entries/future-agi/first-agent/versions/${version}/bundle?source=web`,
      );
      expect(screen.queryByTestId("hub-use-this")).toBeNull();
      expect(screen.queryByTestId("hub-cli-add")).toBeNull();
      expect(calls.some((call) => call.path.includes("/bundle"))).toBe(false);
    },
  );

  it("keeps run, install, remix and download on the selected old version and registry", async () => {
    entryRoutes([OMEGA_PART.HUB_CLI]);
    entryPage(`?version=${OLD_VERSION}`);
    const run = await screen.findByTestId("hub-cli-run");
    expect(run).toHaveTextContent(
      `omega hub run hub:future-agi/first-agent@${OLD_VERSION} --registry ${REGISTRY}`,
    );
    expect(screen.getByTestId("hub-cli-add")).toHaveTextContent(
      `omega hub add hub:future-agi/first-agent@${OLD_VERSION} --registry ${REGISTRY}`,
    );
    expect(await screen.findByTestId("hub-remixes")).toHaveTextContent(
      `omega hub new my-first-agent --from hub:future-agi/first-agent@${OLD_VERSION} --registry ${REGISTRY}`,
    );
    expect(
      screen.getByRole("link", { name: "Download (.tar.gz)" }),
    ).toHaveAttribute(
      "href",
      `${REGISTRY}/entries/future-agi/first-agent/versions/${OLD_VERSION}/bundle?source=web`,
    );
  });

  it("pins the resolved latest version instead of emitting an unpinned run", async () => {
    entryRoutes([OMEGA_PART.HUB_CLI]);
    entryPage();
    expect(await screen.findByTestId("hub-cli-run")).toHaveTextContent(
      `omega hub run hub:future-agi/first-agent@${official.latest.version} --registry ${REGISTRY}`,
    );
  });

  it("preserves a version pin while following a moved entry", async () => {
    entryRoutes();
    const existingCalls = [
      [
        "get",
        "/omega-hub/v1/entries/team/old-name",
        { movedTo: "team/new-name" },
        301,
      ],
      ["get", "/omega-hub/v1/me", meUser],
    ];
    respond(existingCalls);
    renderAt(<HubEntryPage />, {
      path: "/dashboard/hub/:publisher/:name/:tab?",
      at: `/dashboard/hub/team/old-name/files?version=${OLD_VERSION}`,
    });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        `/dashboard/hub/team/new-name/files?version=${OLD_VERSION}`,
      ),
    );
  });
});

describe("This install's publishing commands", () => {
  it("routes login, publish and status to the same self-hosted registry", async () => {
    respond([
      [
        "get",
        "/api/capabilities/",
        capabilitiesAnswer({
          parts: [OMEGA_PART.HUB_CLI, OMEGA_PART.HUB_PUBLISH],
        }),
      ],
      ["get", "/omega-hub/v1/me", meUser],
      ["get", "/omega-hub/v1/rules", rules],
    ]);
    renderAt(<HubPublishPage />);
    const terminal = await screen.findByTestId("hub-publish-cli");
    expect(terminal).toHaveTextContent(
      `omega hub login --registry ${REGISTRY}`,
    );
    expect(terminal).toHaveTextContent(
      `omega hub publish ./my-entry --wait --registry ${REGISTRY}`,
    );
    expect(terminal).toHaveTextContent(
      `omega hub status --registry ${REGISTRY}`,
    );
  });

  it("tells the person to send their token to this registry, never the default", () => {
    respond([["get", "/omega-hub/v1/me/tokens", []]]);
    renderAt(<TokenSettings publishers={[]} />);
    expect(
      screen.getByTestId("hub-tokens").querySelector("code"),
    ).toHaveTextContent(`omega hub login --registry ${REGISTRY}`);
    expect(screen.getByTestId("hub-tokens")).toHaveTextContent(
      "In CI, add --token-stdin to this command and pipe the token in.",
    );
    expect(calls.some((call) => call.method === "post")).toBe(false);
  });
});

function browse(at = "/dashboard/hub") {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
    ["get", "/omega-hub/v1/entries", { count: 0, results: [], facets: {} }],
    ["get", "/omega-hub/v1/picks", []],
    ["get", "/omega-hub/v1/me", meUser],
  ]);
  return renderAt(<HubBrowsePage />, { at });
}

describe("Empty self-hosted registry (D6)", () => {
  it("explains ownership and links to the public catalogue without fetching it", async () => {
    browse();
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent(
      "This Hub is your organisation's own registry.",
    );
    expect(empty).toHaveTextContent(
      "Official entries are available in the public Omega Hub catalogue",
    );
    const link = within(empty).getByRole("link", {
      name: "Browse the public Omega Hub catalogue",
    });
    expect(link).toHaveAttribute("href", CATALOGUE);
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(calls.every((call) => call.path.startsWith("/"))).toBe(true);
  });

  it.each([
    "https://api.futureagi.com",
    "https://api-us.futureagi.com",
    "https://api-eu.futureagi.com",
    "https://dev.api.futureagi.com",
    "https://api.futureagi.com/proxy",
  ])("keeps hosted %s free of self-hosted instructions", async (apiBase) => {
    config.apiBase = apiBase;
    browse();
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent("The Hub has no entries");
    expect(empty).toHaveTextContent("Approved entries appear here.");
    // The empty registry promises nothing about when entries arrive.
    expect(empty).not.toHaveTextContent(/\byet\b/);
    expect(empty).not.toHaveTextContent("organisation's own registry");
    expect(
      within(empty).queryByRole("link", { name: /public Omega Hub catalogue/ }),
    ).toBeNull();
    // What to do next on an empty hosted Hub: publish the first entry.
    expect(
      within(empty).getByRole("link", { name: "Publish an entry" }),
    ).toHaveAttribute("href", "/dashboard/hub/publish");
  });

  it("keeps filtered no-results distinct from an empty registry", async () => {
    browse("/dashboard/hub?q=missing");
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent("Nothing matches “missing”");
    expect(empty).toHaveTextContent(
      "Search looks at each entry's title, tagline, tags, publisher and README.",
    );
    expect(
      within(empty).queryByRole("link", { name: /public Omega Hub catalogue/ }),
    ).toBeNull();
    fireEvent.click(
      within(empty).getByRole("button", { name: "Clear search" }),
    );
    expect(
      await screen.findByRole("link", {
        name: "Browse the public Omega Hub catalogue",
      }),
    ).toHaveAttribute("href", CATALOGUE);
  });
});
