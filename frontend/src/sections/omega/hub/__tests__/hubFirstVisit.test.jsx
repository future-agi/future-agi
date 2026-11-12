import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import HubBrowsePage from "../HubBrowsePage";
import HubEntryPage from "../HubEntryPage";
import HubPublishersPage from "../HubPublishersPage";
import CopyCommand from "../components/CopyCommand";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// Hub browse and entry page layout.
// Staging has the Hub CLI part released and a new Hub: every entry has 0
// likes and 0 installs. These cases render that state the way a first-time
// visitor sees it and pin what the screenshots showed broken: "♡ 0" and
// "⬇ —" on every card, three stacked control rows, flat cards with no
// heading, commands cut in the middle, an empty band above the entry's rail,
// "Like 0" with Moderate beside it, an oversized version picker, the README
// repeating the page title, "No installs yet" in the rail's best place and
// "0 likes · — installs" on the publishers index.

const CLI = [OMEGA_PART.HUB_CLI];
const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const DASH = "—";

const capabilities = (parts) => [
  "get",
  "/api/capabilities/",
  capabilitiesAnswer({ parts }),
];

// Let /api/capabilities/ answer, so a gate that would open has had its chance.
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

const card = (extra = {}) => ({
  ...structuredClone(official),
  likes: 0,
  installs30d: 0,
  ...extra,
});

const NEW_HUB_KINDS = {
  agent: 1,
  team: 0,
  app: 0,
  workflow: 0,
  mcp: 0,
  skill: 0,
  plugin: 0,
};

function serveBrowse(parts, results = [card()], kinds = NEW_HUB_KINDS) {
  respond([
    capabilities(parts),
    [
      "get",
      "/omega-hub/v1/entries",
      { count: results.length, results, facets: { kinds }, sort: "new" },
    ],
    ["get", "/omega-hub/v1/picks", []],
    ["get", "/omega-hub/v1/me", meUser],
  ]);
}

const openBrowse = (at = "/dashboard/hub") =>
  renderAt(<HubBrowsePage />, { at });

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Hub home: a new Hub reads as new, not broken", () => {
  it("a card with nothing counted shows no '♡ 0' and no '⬇ —'", async () => {
    serveBrowse(CLI);
    openBrowse();
    const entryCard = await screen.findByTestId("hub-entry-card");
    await settle();
    expect(within(entryCard).queryByLabelText(/^Likes:/)).toBeNull();
    expect(
      within(entryCard).queryByLabelText(/^Installs, last 30 days:/),
    ).toBeNull();
    const meta = within(entryCard).getByTestId("hub-entry-card-meta");
    // The latest version is the one number a new card shows.
    const version = within(meta).getByTestId("hub-entry-card-version");
    expect(version).toHaveTextContent("v1.0.0");
    const counted = meta.textContent.replace(version.textContent, "");
    expect(counted).not.toContain(DASH);
    expect(counted).not.toMatch(/(^|\D)0(\D|$)/);
  });

  it("a card shows the counts the server did count", async () => {
    serveBrowse(CLI, [card({ likes: 3, installs30d: 7 })]);
    openBrowse();
    expect(await screen.findByLabelText("Likes: 3")).toBeInTheDocument();
    expect(
      await screen.findByLabelText("Installs, last 30 days: 7"),
    ).toBeInTheDocument();
  });

  it("a card leads with its title as a heading that links to the entry, then what it does; kind and tier are quiet metadata", async () => {
    serveBrowse(CLI);
    openBrowse();
    const entryCard = await screen.findByTestId("hub-entry-card");
    const heading = within(entryCard).getByRole("heading", {
      level: 3,
      name: official.title,
    });
    // e2e filters cards by their reference, so it stays on the card.
    expect(entryCard).toHaveTextContent(official.ref);
    expect(
      within(entryCard).getByTestId("hub-entry-card-tagline"),
    ).toHaveTextContent(official.tagline);
    const meta = within(entryCard).getByTestId("hub-entry-card-meta");
    expect(meta).toHaveTextContent("Agent");
    const badge = within(meta).getByTestId("hub-tier-badge");
    expect(badge).toHaveAttribute("data-tier", "official");
    expect(badge).toHaveAttribute("data-quiet", "true");
    // The way in is the title itself, so the card can also hold "Use".
    expect(
      within(heading).getByRole("link", { name: official.title }),
    ).toHaveAttribute("href", "/dashboard/hub/future-agi/first-agent");
  });

  it("says what a visitor gets first; how review works is one quiet line under it", async () => {
    serveBrowse(CLI);
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Working agents you can run in one command",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /^Ready-to-run agents, teams, apps, workflows, MCP servers, skills and plugins for Omega\./,
      ),
    ).toBeInTheDocument();
    // Review is said once, and never in the lede.
    expect(
      screen.getAllByText(/Every version is checked automatically/),
    ).toHaveLength(1);
    expect(screen.getByText(/^Ready-to-run agents/).textContent).not.toMatch(
      /checked automatically/,
    );
  });
});

describe("Hub home: one control row", () => {
  it("the search field, the kinds, trust, 'Runs without a key' and the sort share one row; a kind with nothing listed shows no count", async () => {
    serveBrowse(CLI);
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    const row = screen.getByTestId("hub-filters");
    expect(within(row).getByTestId("hub-search")).toBeInTheDocument();
    expect(
      within(row).getByRole("combobox", { name: "Sort" }),
    ).toBeInTheDocument();
    expect(within(row).getByTestId("hub-filter-nokey")).toBeInTheDocument();
    expect(within(row).getByTestId("hub-filter-kind-agent")).toHaveTextContent(
      "Agents1",
    );
    // Every kind stays reachable; one with nothing listed reads "Plugins",
    // never "Plugins 0".
    for (const kind of ["team", "app", "workflow", "mcp", "skill", "plugin"]) {
      expect(
        within(row).getByTestId(`hub-filter-kind-${kind}`).textContent,
      ).not.toMatch(/\d/);
    }
    // Trust is one select in the row, even when every entry has one trust
    // level; the tiers are never a second row of chips.
    expect(
      within(row).getByRole("combobox", { name: "Trust" }),
    ).toHaveTextContent("Any publisher");
    expect(screen.queryByTestId("hub-filter-tier-official")).toBeNull();
  });

  it("keeps a selected kind in view even when nothing is listed under it", async () => {
    serveBrowse(CLI, [], NEW_HUB_KINDS);
    openBrowse("/dashboard/hub?kind=plugin");
    expect(await screen.findByTestId("hub-filter-kind-plugin")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("trust offers every level even when every entry has the same one, and filters the list", async () => {
    serveBrowse(CLI);
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Trust" }));
    for (const level of ["Official", "Verified publisher"]) {
      expect(
        await screen.findByRole("option", { name: level }),
      ).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("option", { name: "Community reviewed" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "tier=community",
      ),
    );
  });

  it("a search applies once typing pauses, and keeps the space being typed", async () => {
    serveBrowse(CLI);
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    const box = screen.getByTestId("hub-search");
    fireEvent.change(box, { target: { value: "sql" } });
    await waitFor(
      () => expect(screen.getByTestId("location")).toHaveTextContent("q=sql"),
      { timeout: 3000 },
    );
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.path === "/omega-hub/v1/entries" && call.params?.q === "sql",
        ),
      ).toBe(true),
    );
    fireEvent.change(box, { target: { value: "sql " } });
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(box).toHaveValue("sql ");
    expect(screen.getByTestId("location")).toHaveTextContent("q=sql");
  });
});

describe("A copyable command is shown whole", () => {
  it("wraps instead of cutting its middle, and copies the full text", async () => {
    const text = `omega hub add hub:future-agi/first-agent@1.0.0 --registry https://fa.example.test/__api/omega-hub/v1`;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    respond([]);
    renderAt(<CopyCommand text={text} testId="cmd" />);
    const code = screen.getByTestId("cmd").querySelector("code");
    expect(code.textContent).toBe(text);
    expect([...code.children].every((node) => node.tagName === "WBR")).toBe(
      true,
    );
    expect(code).toHaveAttribute("title", text);
    expect(getComputedStyle(code).whiteSpace).toBe("pre-wrap");
    expect(getComputedStyle(code).textOverflow).not.toBe("ellipsis");
    fireEvent.click(screen.getByRole("button", { name: `Copy ${text}` }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(text));
  });
});

function serveEntry(entry, { parts = CLI, me = meUser, rows = versions } = {}) {
  respond([
    capabilities(parts),
    ["get", ENTRY, entry],
    ["get", `${ENTRY}/versions`, rows],
    ["get", `${ENTRY}/versions/0.9.0`, { ...entry.latest, version: "0.9.0" }],
    ["get", `${ENTRY}/remixes`, []],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", me],
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

describe("Entry page: what it does and how to run it come first", () => {
  it("the title block sits above both columns; the rail starts beside the main column, with Use this at its top", async () => {
    serveEntry(structuredClone(official));
    openEntry();
    const useThis = await screen.findByTestId("hub-use-this");
    const main = screen.getByTestId("hub-entry-main");
    const rail = screen.getByTestId("hub-entry-rail");
    const header = screen.getByTestId("hub-entry-header");
    expect(screen.getByTestId("hub-entry")).toContainElement(header);
    expect(
      header.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(rail.parentElement).toBe(main.parentElement);
    expect(main.nextElementSibling).toBe(rail);
    expect(rail.firstElementChild).toContainElement(useThis);
  });

  it("the title is the page heading, and the README does not repeat it", async () => {
    serveEntry(structuredClone(official));
    openEntry();
    const readme = await screen.findByTestId("hub-readme");
    expect(
      screen.getByRole("heading", { level: 1, name: official.title }),
    ).toBeInTheDocument();
    expect(
      within(readme).queryByRole("heading", { name: official.title }),
    ).toBeNull();
    // What it does is its own section above the tabs, not inside the README.
    expect(
      within(readme).queryByRole("heading", { name: "What it does" }),
    ).toBeNull();
    expect(
      screen.getByRole("heading", { level: 2, name: "What it does" }),
    ).toBeInTheDocument();
  });

  it("both commands are whole on the page, in full, wrap, and say what each does", async () => {
    serveEntry(structuredClone(official));
    openEntry();
    for (const [testId, verb, caption] of [
      ["hub-cli-add", "add", "Add it to your project"],
      ["hub-cli-run", "run", "Try it now"],
    ]) {
      const box = await screen.findByTestId(testId);
      const said = screen.getByTestId(`${testId}-caption`);
      expect(said).toHaveTextContent(caption);
      expect(said.nextElementSibling).toBe(box);
      const code = box.querySelector("code");
      const command = `omega hub ${verb} hub:future-agi/first-agent@1.0.0 --registry http://localhost:8000/omega-hub/v1`;
      expect(code.textContent).toBe(command);
      expect(code).toHaveAttribute("title", command);
      expect([...code.children].every((node) => node.tagName === "WBR")).toBe(
        true,
      );
      expect(getComputedStyle(code).whiteSpace).toBe("pre-wrap");
    }
  });

  it("a new entry shows Like with no '0' and no installs panel", async () => {
    serveEntry(structuredClone(official));
    openEntry();
    const like = await screen.findByTestId("hub-like-button");
    await screen.findByTestId("hub-use-this");
    await settle();
    // Signed in (the /me answer is in), so the button reads Like, with no count.
    await waitFor(() => expect(like).toHaveTextContent(/^Like$/));
    expect(screen.queryByTestId("hub-like-count")).toBeNull();
    expect(screen.queryByTestId("hub-installs")).toBeNull();
    expect(screen.getByTestId("hub-entry-rail").textContent).not.toMatch(
      /No installs/,
    );
  });

  it("maintainer actions sit under Maintainer in the title block's More menu", async () => {
    serveEntry(structuredClone(official), { me: meMaintainer });
    openEntry();
    fireEvent.click(await screen.findByRole("button", { name: "More" }));
    const menu = await screen.findByRole("menu");
    expect(await within(menu).findByText("Maintainer")).toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: "Unrank" }),
    ).toBeInTheDocument();
    expect(
      within(menu).getByRole("menuitem", { name: "Report a problem" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("hub-moderate-button")).toBeNull();
  });

  it("one version: no picker; the header's facts line names it, and the commit it came from", async () => {
    const entry = structuredClone(official);
    entry.latest = { ...entry.latest, sourceCommit: "0123abcd9876" };
    serveEntry(entry);
    openEntry();
    const facts = await screen.findByTestId("hub-entry-facts");
    await screen.findByTestId("hub-use-this");
    expect(facts).toHaveTextContent("Version 1.0.0");
    expect(screen.getByTestId("hub-entry-byline")).toHaveTextContent(
      "from commit 0123abc",
    );
    expect(screen.queryByRole("combobox", { name: "Version" })).toBeNull();
  });

  it("several versions: no picker; the Versions tab switches the version", async () => {
    const entry = structuredClone(official);
    serveEntry(entry, {
      rows: [entry.latest, { ...entry.latest, version: "0.9.0" }],
    });
    openEntry();
    await screen.findByTestId("hub-entry-header");
    expect(screen.queryByRole("combobox", { name: "Version" })).toBeNull();
    fireEvent.click(await screen.findByTestId("hub-tab-versions"));
    fireEvent.click(await screen.findByRole("link", { name: "0.9.0" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "?version=0.9.0",
      ),
    );
  });
});

describe("Publishers index on a new Hub", () => {
  it("names entries and the date, with no '0 likes' and no '— installs'", async () => {
    const profile = {
      handle: "future-agi",
      displayName: "Future AGI",
      tier: "official",
      entryCount: 11,
      likes: 0,
      installs30d: 0,
      firstPublishedAt: "2026-11-11T00:00:00Z",
    };
    respond([
      capabilities(CLI),
      ["get", "/omega-hub/v1/publishers", { count: 1, results: [profile] }],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    renderAt(<HubPublishersPage />);
    const row = await screen.findByTestId("hub-publisher-row");
    await settle();
    expect(row).toHaveTextContent("11 entries · since 11 Nov 2026");
    expect(row.textContent).not.toMatch(/likes/);
    expect(row.textContent).not.toMatch(/installs/);
    expect(row.textContent).not.toContain(DASH);
  });
});
