import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { focusPageSearch } from "src/sections/common/ux/pageSearch";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { palette } from "src/theme/palette";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import meUser from "../__fixtures__/me-user.json";
import HubBrowsePage from "../HubBrowsePage";
import { mondayOf } from "../lib/format";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// The Hub home as a visitor meets it. It used to open on "Omega Hub" over
// three rows of chips and tabs, with flat cards that were one big link and
// had no action, the maintainer picks repeated in the grid, a box glyph on
// an empty search and shapeless loading blocks. Each case below renders the
// page the way a visitor meets it and pins what replaced one of those.

const CLI = [OMEGA_PART.HUB_CLI];
const ENTRIES = "/omega-hub/v1/entries";
const STARTER = "/omega-hub/v1/entries/future-agi/first-agent";
const REGISTRY = "--registry http://localhost:8000/omega-hub/v1";
const runOf = (name) =>
  `omega hub run hub:future-agi/${name}@1.0.0 ${REGISTRY}`;
const addOf = (name) =>
  `omega hub add hub:future-agi/${name}@1.0.0 ${REGISTRY}`;
const RUN = runOf("first-agent");
const HEADLINE = "Working agents you can run in one command";
const TRUST =
  "Every version is checked automatically and approved by a Future AGI maintainer.";
const WHY = "Watch the read tool refuse a DELETE.";
const MISSING = {
  error: { code: "OMEGA_HUB_NOT_FOUND", message: "Not found" },
};

const cssRgb = (hex) => {
  const [r, g, b] = hex.match(/[a-f\d]{2}/gi).map((n) => parseInt(n, 16));
  return `rgb(${r}, ${g}, ${b})`;
};

const KINDS = {
  agent: 3,
  team: 0,
  app: 0,
  workflow: 0,
  mcp: 0,
  skill: 0,
  plugin: 0,
};

const card = (name, extra = {}) => ({
  ...structuredClone(official),
  ref: `future-agi/${name}`,
  name,
  title: `Title of ${name}`,
  tagline: `What ${name} does.`,
  likes: 0,
  installs30d: 0,
  noKeyNeeded: false,
  pick: null,
  ...extra,
});

const pickOf = (name, extra = {}) =>
  card(name, { pick: { week: mondayOf(), why: WHY }, ...extra });

const page = (results, extra = {}) => ({
  count: results.length,
  results,
  facets: { kinds: KINDS },
  sort: "new",
  ...extra,
});

const TAGS = [
  { tag: "voice", count: 2 },
  { tag: "sql", count: 1 },
];

// `starter: null` is a registry that does not list future-agi/first-agent;
// a function answers for it when the case chooses.
function serve({
  parts = CLI,
  results = [],
  picks = [],
  entries,
  starter = structuredClone(official),
} = {}) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", ENTRIES, entries || page(results)],
    ["get", "/omega-hub/v1/picks", picks],
    starter ? ["get", STARTER, starter] : ["get", STARTER, MISSING, 404],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/tags", TAGS],
    ["get", "/omega-hub/v1/me", meUser],
  ]);
}

// An answer that arrives when the case releases it.
function held() {
  let release;
  const answer = new Promise((done) => {
    release = done;
  });
  return { answer, release };
}

const openBrowse = (at = "/dashboard/hub") =>
  renderAt(<HubBrowsePage />, { at });

// Let /api/capabilities/ answer, so a gate that would open has had its chance.
const settle = () => new Promise((done) => setTimeout(done, 50));

const address = () => screen.getByTestId("location");

const entryRequests = () =>
  calls
    .filter((call) => call.path.startsWith(`${ENTRIES}/`))
    .map((call) => call.path);

// The entries drawn inside `node`, by reference, in order.
const refsIn = (node) =>
  [...node.querySelectorAll("[data-ref]")].map((el) => el.dataset.ref);

let writeText;
beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
});
afterEach(() => vi.unstubAllEnvs());

describe("Hub home: the first screen says what you get", () => {
  it("leads with the headline, keeps the subtitle as its lede, and puts review in one quiet line that opens the rules' own words", async () => {
    serve({ results: [card("sql-analyst")] });
    openBrowse();
    expect(
      await screen.findByRole("heading", { level: 1, name: HEADLINE }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /^Ready-to-run agents, teams, apps, workflows, MCP servers, skills and plugins for Omega\./,
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText(TRUST)).toHaveLength(1);
    const toggle = await screen.findByRole("button", {
      name: "What review means",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // Closed, the rules text is not on the page, so the toggle points at
    // nothing.
    expect(toggle).not.toHaveAttribute("aria-controls");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    for (const text of rules.reviewMeans) {
      expect(await screen.findByText(text)).toBeVisible();
    }
    const means = document.getElementById(toggle.getAttribute("aria-controls"));
    expect(means).toHaveTextContent(rules.reviewMeans[0]);
    toggle.focus();
    fireEvent.keyDown(toggle, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(toggle);
  });

  it("offers the first agent's whole run command to try now, with copy and a way to its page", async () => {
    serve({ results: [card("sql-analyst")] });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    expect(
      within(box).getByRole("heading", { level: 2, name: "Try one now" }),
    ).toBeInTheDocument();
    const command = await within(box).findByTestId("hub-try-one-run");
    expect(command.querySelector("code").textContent).toBe(RUN);
    fireEvent.click(within(box).getByRole("button", { name: `Copy ${RUN}` }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(RUN));
    expect(
      within(box).getByRole("link", { name: "See the entry" }),
    ).toHaveAttribute("href", "/dashboard/hub/future-agi/first-agent");
  });

  it("with no command to offer, still names the first agent and links to it, and names no command anywhere", async () => {
    serve({ parts: [], results: [card("sql-analyst")] });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    await settle();
    expect(
      within(box).getByRole("link", { name: official.title }),
    ).toHaveAttribute("href", "/dashboard/hub/future-agi/first-agent");
    expect(screen.queryByTestId("hub-try-one-run")).toBeNull();
    expect(screen.queryByTestId("hub-entry-use")).toBeNull();
    expect(document.body).not.toHaveTextContent(/omega hub/);
    expect(screen.getByRole("heading", { level: 1 }).textContent).not.toMatch(
      /one command/,
    );
    expect(
      within(box).getByRole("heading", { name: "Start with this one" }),
    ).toBeInTheDocument();
  });

  it("where the registry does not list the first agent, offers this week's first pick with its why, from what the page already loaded, and draws it nowhere else", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({
      starter: null,
      results: [picked, card("notes-plugin", { title: "A notes plugin" })],
      picks: [picked],
    });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    const command = await within(box).findByTestId("hub-try-one-run");
    expect(command.querySelector("code").textContent).toBe(
      runOf("sql-analyst"),
    );
    expect(
      within(box).getByRole("link", { name: "See the entry" }),
    ).toHaveAttribute("href", "/dashboard/hub/future-agi/sql-analyst");
    expect(within(box).getByTestId("hub-try-one-why")).toHaveTextContent(
      `Maintainer pick: ${WHY}`,
    );
    // It was the only pick, so Start here is not drawn and the list holds
    // every other entry.
    expect(
      await screen.findByRole("heading", { level: 2, name: "All entries" }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("hub-picks")).toBeNull();
    expect(refsIn(screen.getByTestId("hub-entry-grid"))).toEqual([
      "future-agi/notes-plugin",
    ]);
    await settle();
    // No request beyond the first agent's own: the pick was already loaded.
    expect(entryRequests()).toEqual([STARTER]);
  });

  it("with no first agent and no picks, offers the first listed agent that runs without a key, and keeps it while the list is searched", async () => {
    const keyless = card("helper", { title: "A helper", noKeyNeeded: true });
    serve({
      starter: null,
      entries: ({ config }) =>
        page(config?.params?.q ? [] : [card("needs-key"), keyless]),
    });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    const command = await within(box).findByTestId("hub-try-one-run");
    expect(command.querySelector("code").textContent).toBe(runOf("helper"));
    expect(box).toHaveTextContent("Runs without a key");
    expect(refsIn(await screen.findByTestId("hub-entry-grid"))).toEqual([
      "future-agi/needs-key",
    ]);
    fireEvent.change(screen.getByTestId("hub-search"), {
      target: { value: "zzzz-nothing" },
    });
    await screen.findByTestId("hub-empty", {}, { timeout: 3000 });
    expect(
      screen.getByTestId("hub-try-one-run").querySelector("code").textContent,
    ).toBe(runOf("helper"));
    expect(entryRequests()).toEqual([STARTER]);
  });

  it("while the entry to try is chosen, holds its place and the list, so nothing below moves when it arrives", async () => {
    const first = held();
    serve({ results: [card("sql-analyst")], starter: () => first.answer });
    openBrowse();
    await waitFor(() =>
      expect(calls.some((call) => call.path === ENTRIES)).toBe(true),
    );
    await settle();
    expect(screen.getByTestId("hub-try-one-loading")).toBeInTheDocument();
    expect(screen.getByTestId("hub-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-entry-card")).toBeNull();
    first.release(structuredClone(official));
    expect(await screen.findByTestId("hub-try-one")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-try-one-loading")).toBeNull();
    expect(await screen.findByTestId("hub-entry-card")).toHaveAttribute(
      "data-ref",
      "future-agi/sql-analyst",
    );
  });

  it("with nothing that can be tried in one command, the headline stands alone and no empty box is drawn", async () => {
    serve({ starter: null, results: [card("needs-key")] });
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    await settle();
    expect(
      screen.getByRole("heading", { level: 1, name: HEADLINE }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("hub-try-one")).toBeNull();
    expect(screen.queryByTestId("hub-try-one-loading")).toBeNull();
  });
});

describe("Hub home: one control row", () => {
  it("holds the search field, the kinds, trust and the sort", async () => {
    serve({ results: [card("a"), card("b")] });
    openBrowse();
    await screen.findAllByTestId("hub-entry-card");
    const row = screen.getByTestId("hub-filters");
    expect(
      within(row).getByRole("searchbox", { name: "Search the Hub" }),
    ).toBeInTheDocument();
    expect(
      within(row).getByRole("group", { name: "Kind" }),
    ).toBeInTheDocument();
    expect(
      within(row).getByRole("combobox", { name: "Trust" }),
    ).toHaveTextContent("Any publisher");
    const sort = within(row).getByRole("combobox", { name: "Sort" });
    await waitFor(() => expect(sort).toHaveTextContent("New"));
  });

  it("offers every trust level even when every listed entry has one, and the choice filters the list", async () => {
    serve({ results: [card("a"), card("b")] });
    openBrowse();
    await screen.findAllByTestId("hub-entry-card");
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Trust" }));
    const listbox = await screen.findByRole("listbox");
    expect(
      within(listbox)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual([
      "Any publisher",
      "Official",
      "Verified publisher",
      "Community reviewed",
    ]);
    fireEvent.click(
      within(listbox).getByRole("option", { name: "Verified publisher" }),
    );
    await waitFor(() => expect(address()).toHaveTextContent("tier=verified"));
    expect(screen.getByRole("combobox", { name: "Trust" })).toHaveTextContent(
      "Verified publisher",
    );
  });

  it("keeps every sort reachable in the select, opens on the order the server chose, and puts a choice in the address", async () => {
    serve({ results: [card("a")] });
    openBrowse();
    const sort = await screen.findByRole("combobox", { name: "Sort" });
    await waitFor(() => expect(sort).toHaveTextContent("New"));
    await settle();
    fireEvent.mouseDown(sort);
    const listbox = await screen.findByRole("listbox");
    expect(
      within(listbox)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Trending", "New", "Most liked", "Most installed"]);
    fireEvent.click(
      within(listbox).getByRole("option", { name: "Most liked" }),
    );
    await waitFor(() => expect(address()).toHaveTextContent("sort=likes"));
    const first = calls.find((call) => call.path === ENTRIES);
    expect(first.params?.sort).toBeUndefined();
  });
});

describe("Hub home: Start here, then More entries", () => {
  it("shows the maintainer picks with their why under Start here, says when they were picked, and does not repeat them below", async () => {
    const week = mondayOf();
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({
      results: [picked, card("notes-plugin", { title: "A notes plugin" })],
      picks: [picked],
    });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    expect(
      within(start).getByRole("heading", { level: 2, name: "Start here" }),
    ).toBeInTheDocument();
    expect(start).toHaveTextContent(WHY);
    const when = within(start).getByText("this week");
    expect(when.tagName).toBe("TIME");
    expect(when).toHaveAttribute("datetime", week);
    expect(when.getAttribute("title")).toMatch(/UTC/);
    expect(
      await screen.findByRole("heading", { level: 2, name: "More entries" }),
    ).toBeInTheDocument();
    const grid = screen.getByTestId("hub-entry-grid");
    expect(within(grid).queryByText("A SQL analyst")).toBeNull();
    expect(within(grid).getByText("A notes plugin")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "A SQL analyst" })).toHaveLength(
      1,
    );
  });

  it("draws the entry offered in Try one now only there: not again in Start here, and not in the list below", async () => {
    const first = pickOf("first-agent", {
      title: official.title,
      tagline: official.tagline,
    });
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({
      results: [
        first,
        picked,
        card("notes-plugin", { title: "A notes plugin" }),
      ],
      picks: [first, picked],
    });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    expect(box).toHaveAttribute("data-ref", "future-agi/first-agent");
    // Start here leaves it out, so the box keeps the maintainer's why.
    expect(within(box).getByTestId("hub-try-one-why")).toHaveTextContent(
      `Maintainer pick: ${WHY}`,
    );
    expect(refsIn(await screen.findByTestId("hub-picks-grid"))).toEqual([
      "future-agi/sql-analyst",
    ]);
    expect(refsIn(screen.getByTestId("hub-entry-grid"))).toEqual([
      "future-agi/notes-plugin",
    ]);
    expect(
      document.querySelectorAll('[data-ref="future-agi/first-agent"]'),
    ).toHaveLength(1);
  });

  it("gives a pick card more weight: the maintainer's why in primary text, with the tagline kept under it", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({ results: [picked, card("notes-plugin")], picks: [picked] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    const pickCard = within(start).getByTestId("hub-entry-card");
    const why = within(pickCard).getByTestId("hub-entry-card-why");
    const tagline = within(pickCard).getByTestId("hub-entry-card-tagline");
    expect(why).toHaveTextContent(WHY);
    expect(tagline).toHaveTextContent("What sql-analyst does.");
    expect(why.nextElementSibling).toBe(tagline);
    const { text } = palette("light");
    expect(getComputedStyle(why).color).toBe(cssRgb(text.primary));
    expect(getComputedStyle(tagline).color).toBe(cssRgb(text.secondary));
  });

  it("while the picks load, promises no Start here and holds the list, so no card is drawn and then taken out", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    const thisWeek = held();
    serve({
      results: [picked, card("notes-plugin", { title: "A notes plugin" })],
      picks: () => thisWeek.answer,
    });
    openBrowse();
    await waitFor(() =>
      expect(calls.some((call) => call.path === ENTRIES)).toBe(true),
    );
    await settle();
    const loading = screen.getByTestId("hub-picks-loading");
    expect(
      within(loading).getAllByTestId("hub-entry-card-skeleton").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Start here" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "All entries" })).toBeNull();
    expect(screen.getByTestId("hub-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-entry-card")).toBeNull();
    thisWeek.release([picked]);
    expect(
      await screen.findByRole("heading", { level: 2, name: "Start here" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "More entries" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "All entries" })).toBeNull();
    const grid = screen.getByTestId("hub-entry-grid");
    expect(within(grid).queryByText("A SQL analyst")).toBeNull();
    expect(within(grid).getByText("A notes plugin")).toBeInTheDocument();
  });
});

describe("Hub home: a card has a hierarchy and an action", () => {
  it("puts the cover beside the title link, the reference in mono with copy, then the tagline, kind, tier and version as metadata, and Runs without a key", async () => {
    serve({
      results: [
        card("sql-analyst", { title: "A SQL analyst", noKeyNeeded: true }),
        card("other", { noKeyNeeded: false }),
      ],
    });
    openBrowse();
    const [first, other] = await screen.findAllByTestId("hub-entry-card");
    expect(first).not.toHaveAttribute("href");
    expect(within(first).getByTestId("hub-entry-cover")).toBeInTheDocument();
    const heading = within(first).getByRole("heading", {
      level: 3,
      name: "A SQL analyst",
    });
    expect(
      within(heading).getByRole("link", { name: "A SQL analyst" }),
    ).toHaveAttribute("href", "/dashboard/hub/future-agi/sql-analyst");
    expect(within(first).getByTestId("hub-entry-card-ref")).toHaveTextContent(
      "future-agi/sql-analyst",
    );
    fireEvent.click(
      within(first).getByRole("button", {
        name: "Copy reference future-agi/sql-analyst",
      }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("future-agi/sql-analyst"),
    );
    expect(
      within(first).getByTestId("hub-entry-card-tagline"),
    ).toHaveTextContent("What sql-analyst does.");
    const meta = within(first).getByTestId("hub-entry-card-meta");
    expect(meta).toHaveTextContent("Agent");
    expect(within(meta).getByTestId("hub-tier-badge")).toHaveTextContent(
      "Official",
    );
    expect(
      within(meta).getByTestId("hub-entry-card-version"),
    ).toHaveTextContent("v1.0.0");
    expect(first).toHaveTextContent("Runs without a key");
    expect(other).not.toHaveTextContent("Runs without a key");
  });

  it("shows the whole reference: a long one wraps instead of being cut, with a copy button beside it", async () => {
    const name = "serve-your-tools-over-mcp-with-a-refund-policy";
    const ref = `future-agi/${name}`;
    serve({ results: [card(name)] });
    openBrowse();
    const entryCard = await screen.findByTestId("hub-entry-card");
    const shown = within(entryCard).getByTestId("hub-entry-card-ref");
    expect(shown.textContent).toBe(ref);
    expect(getComputedStyle(shown).whiteSpace).not.toBe("nowrap");
    expect(getComputedStyle(shown).textOverflow).not.toBe("ellipsis");
    expect(
      within(entryCard).getByRole("button", { name: `Copy reference ${ref}` }),
    ).toBeInTheDocument();
  });

  it("Use opens both whole commands, each with copy; Escape closes it and focus goes back to Use", async () => {
    const runText = runOf("sql-analyst");
    const addText = addOf("sql-analyst");
    serve({ results: [card("sql-analyst", { title: "A SQL analyst" })] });
    openBrowse();
    const entryCard = await screen.findByTestId("hub-entry-card");
    const use = await within(entryCard).findByRole("button", {
      name: "Use A SQL analyst",
    });
    expect(use).toHaveAttribute("aria-expanded", "false");
    use.focus();
    fireEvent.click(use);
    const dialog = await screen.findByRole("dialog", {
      name: "Use A SQL analyst",
    });
    expect(use).toHaveAttribute("aria-expanded", "true");
    const run = within(dialog).getByTestId("hub-entry-use-run");
    const add = within(dialog).getByTestId("hub-entry-use-add");
    expect(run.querySelector("code").textContent).toBe(runText);
    expect(add.querySelector("code").textContent).toBe(addText);
    fireEvent.click(
      within(dialog).getByRole("button", { name: `Copy ${addText}` }),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(addText));
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(use));
  });
});

describe("Hub home: search and filters", () => {
  it("updates as you type, announces the count, and shows each active filter as a chip with Clear all", async () => {
    const both = [card("sql-analyst"), card("sql-two")];
    const all = [card("a"), card("b"), card("c")];
    serve({
      entries: ({ config }) =>
        config?.params?.q === "sql"
          ? page(both, { sort: "relevance" })
          : page(all),
    });
    openBrowse("/dashboard/hub?kind=agent");
    const count = await screen.findByTestId("hub-result-count");
    expect(count).toHaveAttribute("aria-live", "polite");
    await waitFor(() => expect(count).toHaveTextContent("3 results"));
    fireEvent.change(screen.getByTestId("hub-search"), {
      target: { value: "sql" },
    });
    await waitFor(
      () => expect(count).toHaveTextContent("2 results for “sql”"),
      { timeout: 3000 },
    );
    expect(
      screen.getByRole("button", { name: "Remove filter Search: sql" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Remove filter Kind: Agents" }),
    );
    await waitFor(() => expect(address()).not.toHaveTextContent("kind="));
    expect(address()).toHaveTextContent("q=sql");
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    await waitFor(() =>
      expect(address()).toHaveTextContent(/^\/dashboard\/hub$/),
    );
    await waitFor(() => expect(count).toHaveTextContent("3 entries"));
    expect(screen.getByTestId("hub-search")).toHaveValue("");
  });

  it("while a search from the home loads, says it is searching and shows placeholders, never the home's count or cards as its answer", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    const sql = held();
    const narrower = held();
    serve({
      picks: [picked],
      entries: ({ config }) => {
        if (config?.params?.q === "sql") return sql.answer;
        if (config?.params?.q === "sql-a") return narrower.answer;
        return page([picked, card("notes-plugin")]);
      },
    });
    openBrowse();
    await screen.findByRole("heading", { level: 2, name: "More entries" });
    const count = screen.getByTestId("hub-result-count");
    expect(count).toHaveTextContent("2 entries");
    const box = screen.getByTestId("hub-search");
    fireEvent.change(box, { target: { value: "sql" } });
    await waitFor(() => expect(address()).toHaveTextContent("q=sql"), {
      timeout: 3000,
    });
    expect(count).toHaveTextContent("Searching…");
    expect(count).not.toHaveTextContent(/for “sql”/);
    expect(screen.getByTestId("hub-loading")).toBeInTheDocument();
    expect(screen.queryAllByTestId("hub-entry-card")).toHaveLength(0);
    sql.release(page([picked], { sort: "relevance" }));
    await waitFor(() => expect(count).toHaveTextContent("1 result for “sql”"));
    // Narrowing the same search keeps its cards in view, marked as updating.
    fireEvent.change(box, { target: { value: "sql-a" } });
    await waitFor(() => expect(address()).toHaveTextContent("q=sql-a"), {
      timeout: 3000,
    });
    expect(count).toHaveTextContent("Searching…");
    expect(screen.getByTestId("hub-list")).toHaveAttribute("aria-busy", "true");
    expect(screen.getByTestId("hub-entry-card")).toBeInTheDocument();
    narrower.release(page([picked], { sort: "relevance" }));
    await waitFor(() =>
      expect(count).toHaveTextContent("1 result for “sql-a”"),
    );
    expect(screen.getByTestId("hub-list")).not.toHaveAttribute("aria-busy");
  });

  it("with no match, says what was searched, offers tags, Clear search and Publish your own, with no decorative glyph", async () => {
    serve({
      entries: ({ config }) => page(config?.params?.q ? [] : [card("a")]),
    });
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    const box = screen.getByTestId("hub-search");
    fireEvent.change(box, { target: { value: "zzzz-nothing" } });
    const empty = await screen.findByTestId("hub-empty", {}, { timeout: 3000 });
    expect(
      within(empty).getByRole("heading", {
        level: 3,
        name: "Nothing matches “zzzz-nothing”",
      }),
    ).toBeInTheDocument();
    expect(empty.querySelector("svg")).toBeNull();
    expect(screen.getByTestId("hub-result-count")).toHaveTextContent(
      "0 results for “zzzz-nothing”",
    );
    expect(
      within(empty).getByRole("link", { name: "Publish your own" }),
    ).toHaveAttribute("href", "/dashboard/hub/publish");
    fireEvent.click(
      await within(empty).findByRole("button", { name: "voice" }),
    );
    await waitFor(() => expect(address()).toHaveTextContent("q=voice"));
    expect(document.activeElement).toBe(box);
    const again = await screen.findByTestId("hub-empty");
    fireEvent.click(
      within(again).getByRole("button", { name: "Clear search" }),
    );
    await waitFor(() => expect(address()).not.toHaveTextContent("q="));
    expect(await screen.findByTestId("hub-entry-card")).toBeInTheDocument();
    expect(box).toHaveValue("");
  });

  it("while the next search loads, names neither it nor a count it does not have", async () => {
    const voice = held();
    serve({
      entries: ({ config }) => {
        const q = config?.params?.q;
        if (q === "voice") return voice.answer;
        return page(q ? [] : [card("a")]);
      },
    });
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    fireEvent.change(screen.getByTestId("hub-search"), {
      target: { value: "zzzz-nothing" },
    });
    const empty = await screen.findByTestId("hub-empty", {}, { timeout: 3000 });
    fireEvent.click(
      await within(empty).findByRole("button", { name: "voice" }),
    );
    await waitFor(() => expect(address()).toHaveTextContent("q=voice"));
    await settle();
    expect(
      screen.queryByRole("heading", { name: "Nothing matches “voice”" }),
    ).toBeNull();
    const count = screen.getByTestId("hub-result-count");
    expect(count).not.toHaveTextContent("0 results for “voice”");
    expect(count).toHaveTextContent("Searching…");
    expect(screen.getByTestId("hub-loading")).toBeInTheDocument();
    voice.release(page([card("voice-one"), card("voice-two")]));
    await waitFor(() =>
      expect(count).toHaveTextContent("2 results for “voice”"),
    );
    expect(screen.getAllByTestId("hub-entry-card")).toHaveLength(2);
  });

  it("with a filter that matches nothing, offers the way back from that filter, not search tips or tags that would keep it", async () => {
    serve({
      entries: ({ config }) => page(config?.params?.tier ? [] : [card("a")]),
    });
    openBrowse("/dashboard/hub?tier=verified");
    const empty = await screen.findByTestId("hub-empty");
    expect(
      within(empty).getByRole("heading", {
        level: 3,
        name: "Nothing matches this filter",
      }),
    ).toBeInTheDocument();
    await settle();
    expect(
      within(empty).queryByRole("group", { name: "Tags to search for" }),
    ).toBeNull();
    expect(empty).not.toHaveTextContent(/shorter word/);
    expect(empty).toHaveTextContent("Remove it to see every entry.");
    expect(
      screen.getByRole("button", {
        name: "Remove filter Trust: Verified publisher",
      }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(empty).getByRole("button", { name: "Clear filter" }),
    );
    await waitFor(() => expect(address()).not.toHaveTextContent("tier="));
    expect(await screen.findByTestId("hub-entry-card")).toBeInTheDocument();
  });

  it("with a search and another filter that match nothing, offers no tags, since a tag would keep that filter", async () => {
    serve({
      entries: ({ config }) => page(config?.params?.q ? [] : [card("a")]),
    });
    openBrowse("/dashboard/hub?q=sql&kind=team");
    const empty = await screen.findByTestId("hub-empty");
    expect(
      within(empty).getByRole("heading", {
        level: 3,
        name: "Nothing matches “sql”",
      }),
    ).toBeInTheDocument();
    await settle();
    expect(
      within(empty).queryByRole("group", { name: "Tags to search for" }),
    ).toBeNull();
    expect(empty).toHaveTextContent("Try a shorter word, or remove a filter.");
    expect(
      within(empty).getByRole("button", { name: "Clear filters" }),
    ).toBeInTheDocument();
  });

  it("is a search field the app's '/' key reaches", async () => {
    serve({ results: [card("a")] });
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    const box = screen.getByTestId("hub-search");
    expect(box).toHaveAttribute("type", "search");
    expect(box).toHaveAttribute("aria-keyshortcuts", "/");
    const rect = vi.spyOn(Element.prototype, "getBoundingClientRect");
    rect.mockReturnValue({
      width: 200,
      height: 32,
      top: 0,
      left: 0,
      right: 200,
      bottom: 32,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    expect(focusPageSearch()).toBe(true);
    rect.mockRestore();
    expect(document.activeElement).toBe(box);
  });
});

describe("Hub home: loading, errors, pages and width", () => {
  it("loads with placeholders shaped like the cards, for the list and for Start here, and names no section before it loads", async () => {
    const pending = () => new Promise(() => {});
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: CLI })],
      ["get", ENTRIES, pending],
      ["get", "/omega-hub/v1/picks", pending],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    openBrowse();
    const list = await screen.findByTestId("hub-loading");
    expect(
      within(list).getAllByTestId("hub-entry-card-skeleton").length,
    ).toBeGreaterThan(0);
    const picks = screen.getByTestId("hub-picks-loading");
    expect(
      within(picks).getAllByTestId("hub-entry-card-skeleton").length,
    ).toBeGreaterThan(0);
    expect(screen.getByTestId("hub-try-one-loading")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Start here" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "All entries" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Try one now" })).toBeNull();
  });

  it("says which part did not load and asks again on Try again, with no list heading over it", async () => {
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: CLI })],
      ["get", ENTRIES, {}, 404],
      ["get", "/omega-hub/v1/picks", []],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    openBrowse();
    const error = await screen.findByTestId("hub-error");
    expect(error).toHaveTextContent("The Hub entries did not load");
    expect(error).toHaveTextContent(
      "The Hub registry did not answer. Try again in a moment.",
    );
    expect(
      screen.queryByRole("heading", { level: 2, name: "All entries" }),
    ).toBeNull();
    serve({ results: [card("a")] });
    fireEvent.click(within(error).getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("hub-entry-card")).toBeInTheDocument();
  });

  it("on a later page that holds only this week's picks, keeps the page numbers, so the visitor can page back", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({ entries: page([picked], { count: 50 }), picks: [picked] });
    openBrowse("/dashboard/hub?page=2");
    await screen.findByTestId("hub-picks");
    const pager = await screen.findByRole("navigation", {
      name: "pagination navigation",
    });
    fireEvent.click(
      within(pager).getByRole("button", { name: "Go to page 1" }),
    );
    await waitFor(() => expect(address()).toHaveTextContent("page=1"));
  });

  it("lets the Hub page grow to 1400 px, so four columns fit at 1440", async () => {
    serve({ results: [card("a")] });
    openBrowse();
    await screen.findByTestId("hub-entry-card");
    expect(getComputedStyle(screen.getByTestId("hub-browse")).maxWidth).toBe(
      "1400px",
    );
  });

  it("draws a cover as one flat tint, with no colour blend", () => {
    const source = readFileSync(
      resolve(
        process.cwd(),
        "src/sections/omega/hub/components/EntryCover.jsx",
      ),
      "utf8",
    );
    expect(source).not.toMatch(/gradient/i);
  });
});
