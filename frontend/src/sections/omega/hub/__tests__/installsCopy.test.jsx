import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubBrowsePage from "../HubBrowsePage";
import HubEntryPage from "../HubEntryPage";
import HubPublisherPage from "../HubPublisherPage";
import HubPublishersPage from "../HubPublishersPage";
import MoveDialog from "../components/MoveDialog";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// Installs are counted only when the Omega
// CLI downloads a bundle, and opens only through a "Use this" handoff
// (omega_hub/services/counts.py). Hiding only the entry page's always-empty
// panel would leave the same metric on every card, the publisher pages, the
// "Most installed" sort and the notices. This file renders each of those with
// no part released (nothing can install, nothing counts an open) and reads the
// whole page, then again with HUB_CLI, so one gate (lib/installs.js) is what
// hides them. A count the server did see is still shown.

const INSTALL_WORDS = /\binstall(?:s|ed|able|ing)?\b/i;
const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";

const card = (extra = {}) => ({
  ...structuredClone(official),
  installs30d: 0,
  ...extra,
});

const publisherProfile = (extra = {}) => ({
  handle: "future-agi",
  displayName: "Future AGI",
  tier: "official",
  entryCount: 1,
  likes: 2,
  installs30d: 0,
  firstPublishedAt: "2026-10-01T00:00:00Z",
  entries: [card()],
  ...extra,
});

const capabilities = (parts) => [
  "get",
  "/api/capabilities/",
  capabilitiesAnswer({ parts }),
];

// Let /api/capabilities/ answer, so a gate that would open has had its chance.
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

// The page's own words, without the publisher's README (on the entry page,
// also its What it does section, shown above the tabs).
function pageCopy() {
  const copy = document.body.cloneNode(true);
  copy
    .querySelectorAll(
      '[data-testid="hub-readme"], [data-testid="hub-entry-what-body"]',
    )
    .forEach((node) => node.remove());
  return copy.textContent;
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

function serveBrowse(parts, results = [card()]) {
  respond([
    capabilities(parts),
    [
      "get",
      "/omega-hub/v1/entries",
      // The server names the order it applied when the client sent none (public_views.py).
      { count: results.length, results, facets: {}, sort: "trending" },
    ],
    ["get", "/omega-hub/v1/picks", []],
    ["get", "/omega-hub/v1/me", meUser],
  ]);
}

describe("Hub home with nothing that installs", () => {
  it("cards and sorts name no installs", async () => {
    serveBrowse([]);
    renderAt(<HubBrowsePage />, { at: "/dashboard/hub" });
    await screen.findByText(official.title);
    await settle();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Sort" }));
    const sorts = within(screen.getByRole("listbox"))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(sorts).toEqual(["Trending", "New", "Most liked"]);
    expect(
      screen.queryByLabelText(/^Installs, last 30 days/),
    ).not.toBeInTheDocument();
    expect(pageCopy()).not.toMatch(INSTALL_WORDS);
  });

  it("an old ?sort=installs link reads the default order", async () => {
    serveBrowse([]);
    renderAt(<HubBrowsePage />, { at: "/dashboard/hub?sort=installs" });
    await screen.findByText(official.title);
    await settle();
    const asked = calls.filter((call) => call.path === "/omega-hub/v1/entries");
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.map((call) => call.params?.sort)).not.toContain("installs");
    expect(screen.getByRole("combobox", { name: "Sort" })).toHaveTextContent(
      "Trending",
    );
  });

  it("a card still shows installs the server really counted", async () => {
    serveBrowse([], [card({ installs30d: 7 })]);
    renderAt(<HubBrowsePage />, { at: "/dashboard/hub" });
    expect(
      await screen.findByLabelText("Installs, last 30 days: 7"),
    ).toBeInTheDocument();
  });
});

describe("Hub home once the CLI ships", () => {
  // A card shows installs once there are some; a new entry's
  // card shows no "—" (hubFirstVisit.test.jsx).
  it("offers Most installed and the installs on each card that has some", async () => {
    serveBrowse(
      [OMEGA_PART.HUB_CLI],
      [
        card(),
        card({ ref: "future-agi/counted", name: "counted", installs30d: 7 }),
      ],
    );
    renderAt(<HubBrowsePage />, { at: "/dashboard/hub?sort=installs" });
    expect(
      await screen.findByLabelText("Installs, last 30 days: 7"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("hub-entry-card")).toHaveLength(2);
    expect(
      screen.queryByLabelText("Installs, last 30 days: —"),
    ).not.toBeInTheDocument();
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: "Sort" }));
    expect(await screen.findByTestId("hub-sort-installs")).toHaveTextContent(
      "Most installed",
    );
    await waitFor(() =>
      expect(
        calls
          .filter((call) => call.path === "/omega-hub/v1/entries")
          .map((call) => call.params?.sort),
      ).toContain("installs"),
    );
  });
});

function servePublishers(parts, profile = publisherProfile()) {
  respond([
    capabilities(parts),
    ["get", "/omega-hub/v1/publishers", { count: 1, results: [profile] }],
    ["get", "/omega-hub/v1/publishers/future-agi", profile],
    [
      "get",
      /^\/omega-hub\/v1\/publishers\/future-agi\/entries/,
      { count: 1, results: profile.entries },
    ],
    ["get", "/omega-hub/v1/me", meUser],
  ]);
}

const openPublisher = () =>
  renderAt(<HubPublisherPage />, {
    path: "/dashboard/hub/publishers/:handle",
    at: "/dashboard/hub/publishers/future-agi",
  });

describe("Publishers with nothing that installs", () => {
  it("the publishers index says who publishes, with no installs", async () => {
    servePublishers([]);
    renderAt(<HubPublishersPage />);
    expect(await screen.findByText("Future AGI")).toBeInTheDocument();
    await settle();
    expect(screen.getByText("Who publishes on the Hub.")).toBeInTheDocument();
    expect(pageCopy()).toMatch(/1 entry · 2 likes · since/);
    expect(pageCopy()).not.toMatch(INSTALL_WORDS);
  });

  it("a publisher page shows no installs stat", async () => {
    servePublishers([]);
    openPublisher();
    expect(await screen.findByTestId("hub-publisher")).toHaveTextContent(
      "Future AGI",
    );
    await settle();
    expect(screen.getByText("First published")).toBeInTheDocument();
    expect(pageCopy()).not.toMatch(INSTALL_WORDS);
  });

  it("a publisher page keeps installs the server really counted", async () => {
    servePublishers([], publisherProfile({ installs30d: 5 }));
    openPublisher();
    expect(
      await screen.findByText("Installs, last 30 days"),
    ).toBeInTheDocument();
  });
});

describe("Publishers once the CLI ships", () => {
  it("the index shows 30-day installs once there are some", async () => {
    servePublishers([OMEGA_PART.HUB_CLI], publisherProfile({ installs30d: 5 }));
    renderAt(<HubPublishersPage />);
    expect(
      await screen.findByText(
        "Who publishes on the Hub, by installs over the last 30 days.",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/5 installs \(30 days\)/),
    ).toBeInTheDocument();
  });

  // "— installs (30 days)" read as broken on a new Hub.
  it("a publisher with nothing installed shows no '— installs'", async () => {
    servePublishers([OMEGA_PART.HUB_CLI]);
    renderAt(<HubPublishersPage />);
    expect(
      await screen.findByText(
        "Who publishes on the Hub, by installs over the last 30 days.",
      ),
    ).toBeInTheDocument();
    expect(await screen.findByText("Future AGI")).toBeInTheDocument();
    expect(pageCopy()).toMatch(/1 entry · 2 likes · since/);
    expect(pageCopy()).not.toMatch(/— installs/);
  });
});

function serveEntry(entry, parts) {
  respond([
    capabilities(parts),
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, versions],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", meUser],
    [
      "get",
      /^\/omega-hub\/v1\/publishers\//,
      { ...entry.publisher, entryCount: 3 },
    ],
  ]);
}

const openEntry = () =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at: "/dashboard/hub/future-agi/first-agent",
  });

describe("Entry page notices with nothing that installs", () => {
  it("a not-ranked entry is still found by search, and nothing says it installs", async () => {
    serveEntry(
      {
        ...structuredClone(official),
        ranked: false,
        unrankedReason: "quality",
      },
      [],
    );
    openEntry();
    const notice = await screen.findByTestId("hub-unranked-notice");
    await settle();
    expect(notice).toHaveTextContent(
      "A Future AGI maintainer took this entry out of Trending, New and the picks. It is still found by search.",
    );
    expect(pageCopy()).not.toMatch(INSTALL_WORDS);
  });

  it("a yanked version with no reason says who withdrew it", async () => {
    const entry = structuredClone(official);
    entry.latest = { ...entry.latest, status: "yanked", yankedReason: null };
    serveEntry(entry, []);
    openEntry();
    const notice = await screen.findByTestId("hub-yanked-notice");
    await settle();
    expect(notice).toHaveTextContent(
      "The publisher or a Future AGI maintainer withdrew this version.",
    );
    expect(pageCopy()).not.toMatch(INSTALL_WORDS);
  });

  // The panel waits for a first count, so these cases give
  // the entry one; with nothing counted it is left out (hubFirstVisit.test.jsx).
  it("with a handoff but no CLI, the panel counts opens and names no installs", async () => {
    vi.stubEnv("VITE_OMEGA_HUB_OPEN_STUDIO", "1");
    serveEntry({ ...structuredClone(official), opens30d: 3 }, []);
    openEntry();
    const panel = await screen.findByTestId("hub-installs");
    await settle();
    expect(within(panel).getByTestId("hub-opens-value")).toHaveTextContent("3");
    expect(within(panel).queryByTestId("hub-installs-value")).toBeNull();
    expect(panel.textContent).not.toMatch(INSTALL_WORDS);
  });

  it("with the CLI and no handoff, the panel counts installs and no opens", async () => {
    serveEntry({ ...structuredClone(official), installs30d: 4 }, [
      OMEGA_PART.HUB_CLI,
    ]);
    openEntry();
    expect(await screen.findByTestId("hub-installs-value")).toHaveTextContent(
      "4",
    );
    expect(screen.queryByTestId("hub-opens-value")).toBeNull();
  });
});

describe("Move or rename (maintainers)", () => {
  const renderMove = (parts) => {
    respond([capabilities(parts)]);
    renderAt(
      <MoveDialog
        open
        onClose={() => {}}
        entry={{
          ref: "future-agi/first-agent",
          publisher: { handle: "future-agi" },
          name: "first-agent",
        }}
      />,
    );
  };

  it("names no installs while nothing installs", async () => {
    renderMove([]);
    await settle();
    expect(
      screen.getByText(
        /^Likes, picks and versions move with future-agi\/first-agent\./,
      ),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(INSTALL_WORDS);
  });

  it("names installs once the CLI ships", async () => {
    renderMove([OMEGA_PART.HUB_CLI]);
    expect(
      await screen.findByText(/^Likes, installs, picks and versions move with/),
    ).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
  });
});
