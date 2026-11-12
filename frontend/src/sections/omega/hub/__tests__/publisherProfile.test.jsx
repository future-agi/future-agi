import React from "react";
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import official from "../__fixtures__/entry-official.json";
import meUser from "../__fixtures__/me-user.json";
import HubPublisherPage from "../HubPublisherPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// Hub publisher profile: counts show only above zero. The Hub
// quick fixes stopped the cards and the publishers index from showing "0
// likes" and "— installs" on a new Hub; a publisher's own profile still
// opened on a band reading "11 Entries · 0 Likes · — Installs, last 30 days".
// Its tiles now follow the same rule: a count shows once it is above zero,
// the first published date once there is one, and with nothing to say there
// is no band. Installs keep their gate (lib/installs.js), which
// installsCopy.test.jsx and the no-install-copy sweep hold.

const CLI = [OMEGA_PART.HUB_CLI];
const DASH = "—";

const card = () => ({ ...structuredClone(official), likes: 0, installs30d: 0 });

const profile = (extra = {}) => ({
  handle: "future-agi",
  displayName: "Future AGI",
  tier: "official",
  entryCount: 11,
  likes: 0,
  installs30d: 0,
  firstPublishedAt: "2026-11-11",
  entries: [card()],
  ...extra,
});

function serve(body, parts = CLI) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", `/omega-hub/v1/publishers/${body.handle}`, body],
    [
      "get",
      new RegExp(`^/omega-hub/v1/publishers/${body.handle}/entries`),
      { count: body.entries.length, results: body.entries },
    ],
    ["get", "/omega-hub/v1/me", meUser],
  ]);
}

async function openProfile(body, parts) {
  serve(body, parts);
  renderAt(<HubPublisherPage />, {
    path: "/dashboard/hub/publishers/:handle",
    at: `/dashboard/hub/publishers/${body.handle}`,
  });
  const page = await screen.findByTestId("hub-publisher");
  expect(page).toHaveTextContent(body.displayName);
  // Let /api/capabilities/ answer, so the installs gate has had its chance.
  await new Promise((resolve) => setTimeout(resolve, 50));
  return page;
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("A publisher's profile on a new Hub", () => {
  it("reads its entries and first published date, with no 'Likes 0' and no 'Installs —'", async () => {
    await openProfile(profile());
    expect(screen.queryByText("Likes")).toBeNull();
    expect(screen.queryByText("Installs, last 30 days")).toBeNull();
    expect(screen.queryByText("0")).toBeNull();
    expect(screen.queryByText(DASH)).toBeNull();
    const stats = screen.getByTestId("hub-publisher-stats");
    expect(
      within(stats).getByTestId("hub-publisher-stat-entries"),
    ).toHaveTextContent(/^11\s*Entries$/);
    expect(
      within(stats).getByTestId("hub-publisher-stat-first-published"),
    ).toHaveTextContent(/^11 Nov 2026\s*First published$/);
    expect(within(stats).queryByTestId("hub-publisher-stat-likes")).toBeNull();
    expect(
      within(stats).queryByTestId("hub-publisher-stat-installs"),
    ).toBeNull();
  });

  it("with nothing counted and no published date, shows no band of zeros and dashes", async () => {
    const page = await openProfile(
      profile({
        handle: "c-test",
        displayName: "C Test",
        tier: "community",
        entryCount: 0,
        firstPublishedAt: null,
        entries: [],
      }),
    );
    expect(screen.queryByTestId("hub-publisher-stats")).toBeNull();
    expect(screen.queryByText("0")).toBeNull();
    expect(page.textContent).not.toContain(DASH);
    expect(screen.queryByText("First published")).toBeNull();
    expect(screen.getByText("No listed entries")).toBeInTheDocument();
  });

  it("names one entry and one like in the singular", async () => {
    await openProfile(profile({ entryCount: 1, likes: 1 }));
    expect(screen.getByTestId("hub-publisher-stat-entries")).toHaveTextContent(
      /^1\s*Entry$/,
    );
    expect(screen.getByTestId("hub-publisher-stat-likes")).toHaveTextContent(
      /^1\s*Like$/,
    );
  });
});

describe("A publisher's profile once there are counts", () => {
  it("shows every count above zero beside its label", async () => {
    await openProfile(profile({ entryCount: 4, likes: 3, installs30d: 5 }));
    expect(screen.getByText("Entries").parentElement).toHaveTextContent(
      /^4\s*Entries$/,
    );
    expect(screen.getByText("Likes").parentElement).toHaveTextContent(
      /^3\s*Likes$/,
    );
    expect(
      screen.getByText("Installs, last 30 days").parentElement,
    ).toHaveTextContent(/^5\s*Installs, last 30 days$/);
    expect(screen.getByText("First published")).toBeInTheDocument();
  });

  it("without a way to install, a count the server did see still shows", async () => {
    await openProfile(profile({ installs30d: 2 }), []);
    expect(
      screen.getByText("Installs, last 30 days").parentElement,
    ).toHaveTextContent(/^2\s*Installs, last 30 days$/);
  });
});
