import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { focusPageSearch } from "src/sections/common/ux/pageSearch";
import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { palette } from "src/theme/palette";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import meUser from "../__fixtures__/me-user.json";
import HubBrowsePage from "../HubBrowsePage";
import * as entryGrid from "../components/EntryCard";
import * as filtersBar from "../components/FiltersBar";
import { mondayOf } from "../lib/format";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// The Hub home as a visitor meets it. It used to open on "Omega Hub" over
// three rows of chips and tabs, with flat cards that were one big link and
// had no action, the maintainer picks repeated in the grid, a box glyph on
// an empty search and shapeless loading blocks. Later it still showed picks
// with nothing to show what they do, cards with no action while no command
// could run, a Start here row left half empty, the kinds folded into a
// select at 1440 and 1280, and a starter box that said the same thing twice.
// Then card text that showed a third line after the ellipsis that ended its
// second, references broken in the middle of a name, a pick with no demo
// left half empty beside one with a demo, and a starter box with no action.
// Then a card's reference that showed whole only in the browser's own title,
// on hover alone. Each case below renders the page the way a visitor meets
// it (or checks the rule a layout follows, where a width cannot be rendered
// here) and pins what replaced one of those.

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
// a function answers for it when the case chooses. `extra` adds routes, such
// as a version a card reads.
function serve({
  parts = CLI,
  results = [],
  picks = [],
  entries,
  starter = structuredClone(official),
  extra = [],
} = {}) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", ENTRIES, entries || page(results)],
    ["get", "/omega-hub/v1/picks", picks],
    starter ? ["get", STARTER, starter] : ["get", STARTER, MISSING, 404],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/tags", TAGS],
    ["get", "/omega-hub/v1/me", meUser],
    ...extra,
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

// A selector jsdom cannot match (a pseudo-element) never applies here.
function matches(node, selector) {
  try {
    return node.matches(selector);
  } catch {
    return false;
  }
}

// What the page's style rules declare for `node` outside any media or
// container query, so at every width: `property` as written, or "".
function declared(node, property) {
  let value = "";
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules || [])) {
      if (rule.selectorText && matches(node, rule.selectorText)) {
        value = rule.style.getPropertyValue(property) || value;
      }
    }
  }
  return value;
}

// Whether a Hub page's container query ("hub-page (max-width:899.98px)")
// holds on a page `width` px wide.
function holdsAt(condition, width) {
  const query = /^hub-page\s*\((min|max)-width:\s*([\d.]+)px\)$/.exec(
    condition.trim(),
  );
  if (!query) return false;
  const edge = Number(query[2]);
  return query[1] === "min" ? width >= edge : width <= edge;
}

// What the page's style rules declare for `node` on a Hub page `width` px
// wide: the rules outside any query and those of each container query that
// holds at that width, a later rule winning as it does in a browser.
function declaredAt(node, property, width) {
  let value = "";
  const visit = (rules) => {
    for (const rule of Array.from(rules || [])) {
      if (typeof rule.containerText === "string") {
        if (holdsAt(rule.containerText, width)) visit(rule.cssRules);
      } else if (rule.selectorText && matches(node, rule.selectorText)) {
        value = rule.style.getPropertyValue(property) || value;
      }
    }
  };
  for (const sheet of Array.from(document.styleSheets)) visit(sheet.cssRules);
  return value;
}

// How the grid cell around `entryCard` sits in its row on a Hub page `width`
// px wide: "start" keeps the card's own height; "stretch", or nothing, gives
// it the row's.
const alignAt = (entryCard, width) =>
  declaredAt(entryCard.parentElement, "align-self", width);

const FLEX_WORDS = { none: "0 0", auto: "1 1", initial: "0 1" };

// How a flex item takes a row's spare height or gives up its own: its grow
// and shrink factors, from `flex` or from their own properties.
function flexOf(node) {
  const flex = declared(node, "flex");
  const words = (FLEX_WORDS[flex] || flex).split(" ");
  const [grow = "0", shrink = "1"] = words.filter(Boolean);
  return {
    grow: Number(declared(node, "flex-grow") || grow),
    shrink: Number(declared(node, "flex-shrink") || shrink),
  };
}

// A block of text kept to whole lines: one element that holds only its text,
// clamped to `lines`, which the card neither stretches nor squeezes. Its
// last visible line ends in the ellipsis and no line shows after it, at
// every width (a stretched block shows the lines after the ellipsis).
function expectWholeLines(node, lines) {
  expect(node.children).toHaveLength(0);
  expect(declared(node, "display")).toBe("-webkit-box");
  expect(declared(node, "-webkit-line-clamp")).toBe(String(lines));
  expect(declared(node, "-webkit-box-orient")).toBe("vertical");
  expect(declared(node, "overflow")).toBe("hidden");
  expect(flexOf(node)).toEqual({ grow: 0, shrink: 0 });
}

// Text kept on one line, ending in an ellipsis when it is too long.
function expectOneLine(node) {
  const style = getComputedStyle(node);
  expect(style.whiteSpace).toBe("nowrap");
  expect(style.textOverflow).toBe("ellipsis");
  expect(declared(node, "overflow")).toBe("hidden");
}

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

describe("Hub home: the controls", () => {
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

  it("gives a pick card more weight: the maintainer's why in primary text, with the tagline kept under it, above its recorded demo", async () => {
    const picked = pickOf("sql-analyst", {
      title: "A SQL analyst",
      demoExcerpt: ["run_query ok", "run_write ok"],
    });
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

  it("keeps each reference on one line, never broken inside a name: a long one ends in an ellipsis, and its title, the copy button's name and what it copies give it whole", async () => {
    const name = "serve-your-tools-over-mcp-with-a-refund-policy";
    const from = `future-agi/${name}`;
    const picked = pickOf("summarize-a-week-of-customer-feedback", {
      remixOf: `${from}@1.0.0`,
    });
    serve({ results: [card(name), picked], picks: [picked] });
    openBrowse();
    await screen.findByTestId("hub-picks");
    await screen.findByTestId("hub-entry-grid");
    const cards = screen.getAllByTestId("hub-entry-card");
    expect(cards.map((entryCard) => entryCard.dataset.ref)).toEqual([
      "future-agi/summarize-a-week-of-customer-feedback",
      from,
    ]);
    for (const entryCard of cards) {
      const { ref } = entryCard.dataset;
      const shown = within(entryCard).getByTestId("hub-entry-card-ref");
      expect(shown.textContent).toBe(ref);
      expectOneLine(shown);
      expect(shown).toHaveAttribute("title", ref);
      fireEvent.click(
        within(entryCard).getByRole("button", {
          name: `Copy reference ${ref}`,
        }),
      );
      await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(ref));
    }
    // The entry a pick was derived from is named by its reference too.
    const derived = within(cards[0]).getByText(`Derived from ${from}`);
    expectOneLine(derived);
    expect(derived).toHaveAttribute("title", `Derived from ${from}`);
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

describe("Hub home: a card's reference, whole on hover and on keyboard focus", () => {
  it("lets each card's reference take focus, names it by the whole reference, and shows it whole in a tooltip on hover and on focus; its copy button still copies all of it", async () => {
    const name = "serve-your-tools-over-mcp-with-a-refund-policy";
    const picked = pickOf("summarize-a-week-of-customer-feedback");
    serve({ results: [card(name), picked], picks: [picked] });
    openBrowse();
    await screen.findByTestId("hub-picks");
    await screen.findByTestId("hub-entry-grid");
    const cards = screen.getAllByTestId("hub-entry-card");
    expect(cards.map((entryCard) => entryCard.dataset.ref)).toEqual([
      "future-agi/summarize-a-week-of-customer-feedback",
      `future-agi/${name}`,
    ]);
    for (const entryCard of cards) {
      const { ref } = entryCard.dataset;
      const shown = within(entryCard).getByTestId("hub-entry-card-ref");
      expect(shown.textContent).toBe(ref);
      expectOneLine(shown);
      expect(shown).toHaveAttribute("tabindex", "0");
      expect(shown).toHaveAccessibleName(ref);
      expect(shown).toHaveAttribute("title", ref);
      fireEvent.mouseOver(shown);
      expect((await screen.findByRole("tooltip")).textContent).toBe(ref);
      fireEvent.mouseLeave(shown);
      await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
      act(() => shown.focus());
      expect((await screen.findByRole("tooltip")).textContent).toBe(ref);
      act(() => shown.blur());
      await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
      fireEvent.click(
        within(entryCard).getByRole("button", {
          name: `Copy reference ${ref}`,
        }),
      );
      await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(ref));
    }
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

const DEMO = [
  "you: Which product earns the most? Also mark order 1005 as shipped.",
  "run_query failed: run_query only reads. Send one SELECT statement.",
  "run_write ok",
];

// The bundle of a card's latest version, at the entry page's address.
const bundleOf = (name) =>
  `http://localhost:8000${ENTRIES}/future-agi/${name}/versions/1.0.0/bundle?source=web`;

const titleOf = (entryCard) =>
  within(entryCard).getByTestId("hub-entry-card-title").textContent;

// Spans on a 12-track row, cut into rows; a span that runs past the end of
// a row fails, so every row returned is full.
function rowsOf(spans) {
  const rows = [];
  let row = [];
  let used = 0;
  for (const span of spans) {
    used += span;
    row.push(span);
    expect(used).toBeLessThanOrEqual(12);
    if (used === 12) {
      rows.push(row);
      row = [];
      used = 0;
    }
  }
  expect(row).toEqual([]);
  return rows;
}

describe("Hub home: a pick shows what its recorded demo printed", () => {
  it("puts the excerpt after what the pick does, as a quiet mono block labelled From its recorded demo, line by line", async () => {
    const picked = pickOf("sql-analyst", {
      title: "A SQL analyst",
      demoExcerpt: DEMO,
    });
    serve({ results: [picked, card("notes-plugin")], picks: [picked] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    const pickCard = within(start).getByTestId("hub-entry-card");
    const demo = within(pickCard).getByTestId("hub-entry-card-demo");
    expect(demo.tagName).toBe("FIGURE");
    expect(demo.querySelector("figcaption").textContent).toBe(
      "From its recorded demo",
    );
    const lines = within(demo).getAllByTestId("hub-entry-card-demo-line");
    expect(lines.map((line) => line.textContent)).toEqual(DEMO);
    const tagline = within(pickCard).getByTestId("hub-entry-card-tagline");
    const meta = within(pickCard).getByTestId("hub-entry-card-meta");
    expect(
      tagline.compareDocumentPosition(demo) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      demo.compareDocumentPosition(meta) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // One preformatted (mono) block in the card's quiet text colour; no line
    // is split into coloured parts.
    const block = lines[0].parentElement;
    expect(block.tagName).toBe("PRE");
    expect(getComputedStyle(block).color).toBe(
      cssRgb(palette("light").text.secondary),
    );
    for (const line of lines) expect(line.children).toHaveLength(0);
  });

  it("shows the excerpt as plain text: no link, markup or formatting is made from it, and a long line wraps", async () => {
    const odd = [
      "<b>open</b> https://example.test/run?x=1 **done** [docs](https://example.test)",
      `${"x".repeat(119)}…`,
    ];
    const picked = pickOf("sql-analyst", { demoExcerpt: odd });
    serve({ results: [picked, card("notes-plugin")], picks: [picked] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    const demo = within(start).getByTestId("hub-entry-card-demo");
    const lines = within(demo).getAllByTestId("hub-entry-card-demo-line");
    expect(lines.map((line) => line.textContent)).toEqual(odd);
    expect(demo.querySelector("a, b, strong, em, img")).toBeNull();
    expect(within(demo).queryByRole("link")).toBeNull();
    expect(getComputedStyle(lines[0].parentElement).whiteSpace).toBe(
      "pre-wrap",
    );
  });

  it("shows at most four lines, and leaves blank ones out", async () => {
    const six = ["one", "", "two", "three", "four", "five"];
    const picked = pickOf("sql-analyst", { demoExcerpt: six });
    serve({ results: [picked, card("notes-plugin")], picks: [picked] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    expect(
      within(start)
        .getAllByTestId("hub-entry-card-demo-line")
        .map((line) => line.textContent),
    ).toEqual(["one", "two", "three", "four"]);
  });

  it("with no recorded demo, draws no excerpt and no empty demo label", async () => {
    const none = pickOf("sql-analyst", { demoExcerpt: null });
    const unsaid = pickOf("notes-plugin");
    serve({ results: [none, unsaid, card("c")], picks: [none, unsaid] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    expect(within(start).getAllByTestId("hub-entry-card")).toHaveLength(2);
    expect(within(start).queryByTestId("hub-entry-card-demo")).toBeNull();
    expect(start).not.toHaveTextContent("From its recorded demo");
  });
});

describe("Hub home: every card has an action", () => {
  it("without a released command line, every card offers Get the files, the approved .tar.gz at the entry page's address, in its foot", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({
      parts: [],
      results: [picked, card("notes-plugin", { title: "A notes plugin" })],
      picks: [picked],
    });
    openBrowse();
    await screen.findByTestId("hub-picks");
    await settle();
    const cards = screen.getAllByTestId("hub-entry-card");
    expect(cards.map((entryCard) => entryCard.dataset.ref)).toEqual([
      "future-agi/sql-analyst",
      "future-agi/notes-plugin",
    ]);
    for (const entryCard of cards) {
      const { ref } = entryCard.dataset;
      const files = within(entryCard).getByRole("link", {
        name: `Get the files of ${titleOf(entryCard)}`,
      });
      expect(files).toHaveTextContent(/^Get the files$/);
      expect(files).toHaveAttribute("href", bundleOf(ref.split("/")[1]));
      expect(files).toHaveAttribute("target", "_blank");
      expect(files).toHaveAttribute("rel", "noopener noreferrer");
      // The foot is the card's last part, so in a row of equal cards the
      // feet and their actions line up at the bottom.
      const foot = within(entryCard).getByTestId("hub-entry-card-foot");
      expect(foot).toContainElement(files);
      expect(entryCard.lastElementChild).toBe(foot);
      expect(
        within(entryCard).getByRole("button", {
          name: `Copy reference ${ref}`,
        }),
      ).toBeInTheDocument();
      expect(within(entryCard).queryByTestId("hub-entry-use")).toBeNull();
    }
    expect(document.body).not.toHaveTextContent(/omega hub/);
  });

  it("with the command line released, every card offers Use instead, and keeps its copy-reference button", async () => {
    serve({
      results: [
        card("sql-analyst", { title: "A SQL analyst" }),
        card("notes-plugin", { title: "A notes plugin" }),
      ],
    });
    openBrowse();
    await screen.findByRole("button", { name: "Use A SQL analyst" });
    for (const entryCard of screen.getAllByTestId("hub-entry-card")) {
      const foot = within(entryCard).getByTestId("hub-entry-card-foot");
      expect(
        within(foot).getByRole("button", { name: `Use ${titleOf(entryCard)}` }),
      ).toBeInTheDocument();
      expect(within(entryCard).queryByTestId("hub-entry-get-files")).toBeNull();
      expect(
        within(entryCard).getByRole("button", {
          name: `Copy reference ${entryCard.dataset.ref}`,
        }),
      ).toBeInTheDocument();
    }
  });
});

describe("Hub home: Start here fills its row", () => {
  it("lays one pick as one wide card, two as halves, three as thirds and four as quarters where four fit", () => {
    const { fillSpans } = entryGrid;
    expect(fillSpans(1, 4)).toEqual([12]);
    expect(fillSpans(2, 4)).toEqual([6, 6]);
    expect(fillSpans(3, 4)).toEqual([4, 4, 4]);
    expect(fillSpans(4, 4)).toEqual([3, 3, 3, 3]);
    expect(fillSpans(3, 3)).toEqual([4, 4, 4]);
    expect(fillSpans(2, 2)).toEqual([6, 6]);
  });

  it("never leaves a row half empty at 1440, 1280 or 1024 with the sidebar (4, 3 and 2 columns)", () => {
    const { fillSpans } = entryGrid;
    for (const columns of [4, 3, 2]) {
      for (let count = 1; count <= 6; count += 1) {
        const spans = fillSpans(count, columns);
        expect(spans).toHaveLength(count);
        for (const row of rowsOf(spans)) {
          expect(row.length).toBeLessThanOrEqual(columns);
        }
      }
    }
    // More picks than a row holds: the wider cards lead.
    expect(fillSpans(3, 2)).toEqual([12, 6, 6]);
    expect(fillSpans(4, 3)).toEqual([6, 6, 6, 6]);
    expect(fillSpans(5, 4)).toEqual([6, 6, 4, 4, 4]);
    expect(fillSpans(6, 4)).toEqual([4, 4, 4, 4, 4, 4]);
  });
});

describe("Hub home: the kinds are tabs", () => {
  it("puts every kind, with its count, on its own line under the search row", async () => {
    serve({ results: [card("a"), card("b"), card("c")] });
    openBrowse();
    await screen.findAllByTestId("hub-entry-card");
    const controls = screen.getByTestId("hub-filters");
    const row = within(controls).getByTestId("hub-filters-row");
    const line = within(controls).getByTestId("hub-filter-kinds");
    expect(within(row).getByTestId("hub-search")).toBeInTheDocument();
    expect(
      within(row).getByRole("combobox", { name: "Sort" }),
    ).toBeInTheDocument();
    expect(row).not.toContainElement(line);
    expect(
      row.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    const kinds = within(line).getByRole("group", { name: "Kind" });
    expect(
      within(kinds)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual([
      "All",
      "Agents3",
      "Teams",
      "Apps",
      "Workflows",
      "MCP servers",
      "Skills",
      "Plugins",
    ]);
    expect(within(kinds).getByTestId("hub-filter-kind-all")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(within(kinds).getByTestId("hub-filter-kind-team"));
    await waitFor(() => expect(address()).toHaveTextContent("kind=team"));
  });

  it("in search results, shows every kind's count, 0 included, in the tabs and in the select", async () => {
    serve({ results: [card("a"), card("b"), card("c")] });
    openBrowse("/dashboard/hub?q=agent");
    await screen.findAllByTestId("hub-entry-card");
    const counted = [
      "Agents3",
      "Teams0",
      "Apps0",
      "Workflows0",
      "MCP servers0",
      "Skills0",
      "Plugins0",
    ];
    const kinds = within(screen.getByTestId("hub-filter-kinds")).getByRole(
      "group",
      { name: "Kind" },
    );
    expect(
      within(kinds)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["All", ...counted]);
    // The select a narrower page shows in their place reads the same.
    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Kind", hidden: true }),
    );
    const listbox = await screen.findByRole("listbox");
    expect(
      within(listbox)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["Any kind", ...counted]);
  });

  it("keeps the tabs at 1440 and 1280 with the sidebar, and folds them into the select only on a narrower page", () => {
    // The Hub page is about 1150 px wide at 1440 with the sidebar, 990 px
    // at 1280 and 734 px at 1024.
    const { KIND_TABS_FROM } = filtersBar;
    expect(KIND_TABS_FROM).toBeLessThanOrEqual(990);
    expect(KIND_TABS_FROM).toBeGreaterThan(734);
  });
});

describe("Hub home: one control row at 1024 with the sidebar", () => {
  // The Hub page's own width: about 776 px in a 1024 window with the
  // sidebar, 990 px at 1280 and 1150 px at 1440, and about 360 px on a phone.
  const AT_1024 = 776;
  const WIDE = [990, 1150];
  const PHONE = 360;

  async function openControls() {
    serve({ results: [card("a"), card("b")] });
    openBrowse();
    await screen.findAllByTestId("hub-entry-card");
    const row = screen.getByTestId("hub-filters-row");
    const fieldOf = (control) => control.closest(".MuiTextField-root");
    return {
      search: fieldOf(
        within(row).getByRole("searchbox", { name: "Search the Hub" }),
      ),
      // Drawn by a container query only; outside one it is not shown.
      kind: fieldOf(
        within(row).getByRole("combobox", { name: "Kind", hidden: true }),
      ),
      sort: fieldOf(within(row).getByRole("combobox", { name: "Sort" })),
      kinds: screen.getByTestId("hub-filter-kinds"),
    };
  }

  it("lets the search share the line with every control: it starts at its least width and takes the room they leave", async () => {
    const { search } = await openControls();
    expect(declaredAt(search, "flex-basis", AT_1024)).not.toBe("100%");
    const { SEARCH_MIN } = filtersBar;
    expect(SEARCH_MIN).toBeGreaterThanOrEqual(160);
    expect(declaredAt(search, "flex-basis", AT_1024)).toBe(`${SEARCH_MIN}px`);
    expect(declaredAt(search, "min-width", AT_1024)).toBe(`${SEARCH_MIN}px`);
    expect(declaredAt(search, "flex", AT_1024)).toBe("1 1 240px");
    // The controls as a 1024 page draws them (Any kind 91 px, Any publisher
    // 118, Runs without a key 122, the sort at most 150) and the row's four
    // 8 px gaps leave the search at least its least width.
    expect(SEARCH_MIN + 91 + 118 + 122 + 150 + 4 * 8).toBeLessThanOrEqual(
      AT_1024,
    );
  });

  it("keeps every filter in view with its value at 1024: the kinds as a select, the sort only as wide as its value, and no menu holding any of them", async () => {
    const { kind, sort, kinds } = await openControls();
    expect(declaredAt(kind, "display", AT_1024)).toBe("flex");
    expect(declaredAt(kinds, "display", AT_1024)).toBe("none");
    expect(kind).toHaveTextContent("Any kind");
    expect(screen.getByRole("combobox", { name: "Trust" })).toHaveTextContent(
      "Any publisher",
    );
    expect(screen.getByTestId("hub-filter-nokey")).toHaveTextContent(
      "Runs without a key",
    );
    await waitFor(() => expect(sort).toHaveTextContent("Sort: New"));
    expect(declaredAt(sort, "min-width", AT_1024)).not.toBe("150px");
  });

  it("never cuts the search field's hint off mid-word: where the field is narrower than the hint, it ends in an ellipsis", async () => {
    const { search } = await openControls();
    const input = within(search).getByRole("searchbox", {
      name: "Search the Hub",
    });
    expect(input).toHaveAttribute(
      "placeholder",
      "Search entries, tags and publishers",
    );
    for (const width of [AT_1024, ...WIDE, PHONE])
      expect(declaredAt(input, "text-overflow", width)).toBe("ellipsis");
  });

  it("shows the longest search hint that fits the field whole, a shorter one where the field is narrower, and keeps the field's name", async () => {
    // The field's own box, as a browser measures it: each observer of the
    // field hears when its width changes.
    const watchers = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(onResize) {
          this.onResize = onResize;
          this.targets = [];
          watchers.push(this);
        }

        observe(target) {
          this.targets.push(target);
        }

        unobserve() {}

        disconnect() {
          this.targets = [];
        }
      },
    );
    try {
      const { search } = await openControls();
      const input = within(search).getByRole("searchbox", {
        name: "Search the Hub",
      });
      const resizeTo = (width) => {
        Object.defineProperty(input, "clientWidth", {
          configurable: true,
          value: width,
        });
        act(() => {
          for (const watcher of watchers)
            if (watcher.targets.includes(input)) watcher.onResize([]);
        });
      };
      // As wide as at 1440 and 1280, about as wide as at 1024 with the
      // sidebar, and narrower still (a longer sort or trust value beside it).
      resizeTo(640);
      expect(input).toHaveAttribute(
        "placeholder",
        "Search entries, tags and publishers",
      );
      resizeTo(214);
      expect(input).toHaveAttribute(
        "placeholder",
        "Search entries and publishers",
      );
      resizeTo(120);
      expect(input).toHaveAttribute("placeholder", "Search the Hub");
      expect(screen.getByRole("searchbox", { name: "Search the Hub" })).toBe(
        input,
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("draws Runs without a key in the controls' own text colour while it is off, and shows it on with its pressed state and a check mark", async () => {
    await openControls();
    const { text } = palette("light");
    const toggle = screen.getByTestId("hub-filter-nokey");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(getComputedStyle(toggle).color).toBe(cssRgb(text.primary));
    expect(toggle.querySelector("svg")).toBeNull();
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(screen.getByTestId("hub-filter-nokey")).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    const on = screen.getByTestId("hub-filter-nokey");
    expect(getComputedStyle(on).color).toBe(cssRgb(text.primary));
    expect(on.querySelector("svg")).not.toBeNull();
    expect(on).toHaveTextContent("Runs without a key");
  });

  it("keeps the control rows of 1280 and 1440 as they are, and gives the search a line of its own on a phone", async () => {
    const { search, kind, sort, kinds } = await openControls();
    for (const width of WIDE) {
      expect(declaredAt(search, "flex", width)).toBe("1 1 240px");
      expect(declaredAt(search, "flex-basis", width)).toBe("");
      expect(declaredAt(search, "min-width", width)).toMatch(/^0(px)?$/);
      expect(declaredAt(sort, "min-width", width)).toBe("150px");
      expect(declaredAt(kind, "display", width)).toBe("none");
      expect(declaredAt(kinds, "display", width)).toBe("flex");
    }
    expect(declaredAt(search, "flex-basis", PHONE)).toBe("100%");
  });
});

describe("Hub home: the entry to start with says one thing", () => {
  it.each([
    ["Start with this one", []],
    ["Try one now", CLI],
  ])(
    "for this week's pick, says the maintainer's why and not its tagline as well (%s)",
    async (heading, parts) => {
      const first = pickOf("first-agent", {
        title: official.title,
        tagline: official.tagline,
      });
      serve({ parts, results: [first, card("notes-plugin")], picks: [first] });
      openBrowse();
      const box = await screen.findByTestId("hub-try-one");
      expect(
        await within(box).findByRole("heading", { name: heading }),
      ).toBeInTheDocument();
      expect(within(box).getByTestId("hub-try-one-why")).toHaveTextContent(
        `Maintainer pick: ${WHY}`,
      );
      expect(box).not.toHaveTextContent(official.tagline);
    },
  );

  it("for an entry that is not this week's pick, says its tagline", async () => {
    serve({ parts: [], results: [card("notes-plugin")] });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    await settle();
    expect(box).toHaveTextContent(official.tagline);
    expect(within(box).queryByTestId("hub-try-one-why")).toBeNull();
  });
});

describe("Hub home: the list's last row", () => {
  it("fills one empty slot of a short last row with the way to publish, an action that is not an entry and is not counted", async () => {
    serve({ results: ["a", "b", "c", "d", "e"].map((name) => card(name)) });
    openBrowse();
    const grid = await screen.findByTestId("hub-entry-grid");
    const end = within(grid).getByTestId("hub-entry-grid-end");
    expect(end.tagName).toBe("LI");
    expect(grid.lastElementChild).toBe(end);
    expect(within(grid).getAllByTestId("hub-entry-card")).toHaveLength(5);
    await waitFor(() =>
      expect(screen.getByTestId("hub-result-count")).toHaveTextContent(
        /^5 entries$/,
      ),
    );
    // No card, cover, title or reference: it does not read as an entry.
    expect(within(end).queryByRole("article")).toBeNull();
    expect(within(end).queryByRole("heading")).toBeNull();
    expect(within(end).queryByTestId("hub-entry-cover")).toBeNull();
    expect(end.querySelector("[data-ref]")).toBeNull();
    expect(end).toHaveTextContent(
      "Your own agent, team or tool can be listed here once a maintainer approves it.",
    );
    expect(within(end).getByRole("link", { name: "Publish" })).toHaveAttribute(
      "href",
      "/dashboard/hub/publish",
    );
  });

  it("is drawn only where the last row is short: at 2, 3 and 4 columns (1024, 1280 and 1440 with the sidebar)", () => {
    const { lastRowShortAt } = entryGrid;
    expect(lastRowShortAt(7)).toEqual([2, 3, 4]);
    expect(lastRowShortAt(8)).toEqual([3]);
    expect(lastRowShortAt(6)).toEqual([4]);
    expect(lastRowShortAt(12)).toEqual([]);
  });

  it("is absent when the last row is full at every width", async () => {
    const twelve = Array.from({ length: 12 }, (_, i) => card(`entry-${i}`));
    serve({ results: twelve });
    openBrowse();
    const grid = await screen.findByTestId("hub-entry-grid");
    expect(within(grid).getAllByTestId("hub-entry-card")).toHaveLength(12);
    expect(within(grid).queryByTestId("hub-entry-grid-end")).toBeNull();
  });

  it("is not offered among search results", async () => {
    serve({
      entries: ({ config }) =>
        page(config?.params?.q ? [card("sql-one")] : [card("a")]),
    });
    openBrowse("/dashboard/hub?q=sql");
    await waitFor(() =>
      expect(screen.getByTestId("hub-result-count")).toHaveTextContent(
        "1 result for “sql”",
      ),
    );
    expect(screen.getByTestId("hub-entry-card")).toBeInTheDocument();
    expect(screen.queryByTestId("hub-entry-grid-end")).toBeNull();
  });
});

describe("Hub home: Start here and More entries at 1440, 1280 and 1024", () => {
  // The Hub page's own width (its container) in each window with the
  // sidebar, at both widths measured for it, and the columns of cards the
  // page's container queries give there.
  const WINDOWS = [
    { window: 1440, widths: [1150, 1192], columns: 4 },
    { window: 1280, widths: [990, 1032], columns: 3 },
    { window: 1024, widths: [734, 776], columns: 2 },
  ];

  // How many columns `grid` declares on a Hub page `width` px wide.
  function columnsAt(grid, width) {
    const value = declaredAt(grid, "grid-template-columns", width);
    const flat = value.replace(/\s+/g, "");
    if (flat === "minmax(0,1fr)") return 1;
    const repeat = /^repeat\((\d+),minmax\(0,1fr\)\)$/.exec(flat);
    return repeat ? Number(repeat[1]) : Number.NaN;
  }

  // How many of the 12 tracks each card of a filled grid spans on a Hub
  // page `width` px wide, in order.
  function spansAt(grid, width) {
    return [...grid.children].map((item) => {
      const value = declaredAt(item, "grid-column", width);
      return Number(/^span (\d+)$/.exec(value.trim())?.[1]);
    });
  }

  it.each([
    {
      label: "one pick",
      names: ["sql-analyst"],
      rows: { 4: [[12]], 3: [[12]], 2: [[12]] },
    },
    {
      label: "three picks",
      names: ["sql-analyst", "triage-helper", "notes-helper"],
      rows: { 4: [[4, 4, 4]], 3: [[4, 4, 4]], 2: [[12], [6, 6]] },
    },
  ])(
    "with $label, fills every row of Start here at 1440, 1280 and 1024 with the sidebar, leaving no empty slot",
    async ({ names, rows }) => {
      const picks = names.map((name) => pickOf(name));
      serve({ results: [...picks, card("notes-plugin")], picks });
      openBrowse();
      const grid = await screen.findByTestId("hub-picks-grid");
      expect(within(grid).getAllByTestId("hub-entry-card")).toHaveLength(
        names.length,
      );
      for (const { widths, columns } of WINDOWS) {
        for (const width of widths) {
          expect(columnsAt(grid, width)).toBe(12);
          const filled = rowsOf(spansAt(grid, width));
          expect(filled).toEqual(rows[columns]);
          for (const row of filled)
            expect(row.length).toBeLessThanOrEqual(columns);
        }
      }
    },
  );

  it("with three picks and no demo among them, lets every pick take its row's height, so the feet of the row line up", async () => {
    const names = ["triage-helper", "notes-helper", "sql-analyst"];
    const picks = names.map((name) => pickOf(name, { demoExcerpt: null }));
    serve({ results: [...picks, card("notes-plugin")], picks });
    openBrowse();
    const grid = await screen.findByTestId("hub-picks-grid");
    const cards = within(grid).getAllByTestId("hub-entry-card");
    expect(cards).toHaveLength(names.length);
    for (const { widths } of WINDOWS) {
      for (const width of widths) {
        for (const entryCard of cards)
          expect(alignAt(entryCard, width)).not.toBe("start");
      }
    }
  });

  it("with six picks and one demo, lets every pick take its row's height at 1440, 1280 and 1024 with the sidebar, so the feet of each row line up", async () => {
    const shown = pickOf("sql-analyst", { demoExcerpt: DEMO });
    const plain = ["a", "b", "c", "d", "e"].map((name) =>
      pickOf(`plain-${name}`, { demoExcerpt: null }),
    );
    const picks = [shown, ...plain];
    serve({ results: [...picks, card("notes-plugin")], picks });
    openBrowse();
    const grid = await screen.findByTestId("hub-picks-grid");
    const cards = within(grid).getAllByTestId("hub-entry-card");
    expect(refsIn(grid)).toEqual(picks.map((pick) => pick.ref));
    // Two rows of three at 1440 and 1280, three rows of two at 1024; the
    // pick with a demo leads the first.
    const sizes = { 4: [3, 3], 3: [3, 3], 2: [2, 2, 2] };
    for (const { widths, columns } of WINDOWS) {
      for (const width of widths) {
        const filled = rowsOf(spansAt(grid, width));
        expect(filled.map((row) => row.length)).toEqual(sizes[columns]);
        // No pick declares its own alignment: each takes its row's height.
        const kept = cards.map((entryCard) => alignAt(entryCard, width));
        expect(kept).toEqual(cards.map(() => ""));
      }
    }
  });

  it.each([
    { count: 7, shown: { 4: "flex", 3: "flex", 2: "flex" } },
    { count: 8, shown: { 4: "none", 3: "flex", 2: "none" } },
  ])(
    "lays More entries in 2 columns at 1024 with the sidebar, as its container queries give, and ends the grid with the way to publish wherever the last row is short ($count entries)",
    async ({ count, shown }) => {
      const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
      const more = Array.from({ length: count }, (_, i) => card(`entry-${i}`));
      serve({ results: [picked, ...more], picks: [picked] });
      openBrowse();
      expect(
        await screen.findByRole("heading", { level: 2, name: "More entries" }),
      ).toBeInTheDocument();
      const grid = screen.getByTestId("hub-entry-grid");
      expect(within(grid).getAllByTestId("hub-entry-card")).toHaveLength(count);
      const end = within(grid).getByTestId("hub-entry-grid-end");
      expect(grid.lastElementChild).toBe(end);
      for (const { widths, columns } of WINDOWS) {
        for (const width of widths) {
          expect(columnsAt(grid, width)).toBe(columns);
          // In the one empty slot of a short last row, and nowhere else.
          expect(declaredAt(end, "display", width)).toBe(shown[columns]);
          expect(shown[columns] === "flex").toBe(count % columns !== 0);
        }
      }
    },
  );
});

describe("Hub home: a card's text ends on a whole line", () => {
  it("keeps the tagline of every card and pick to whole lines on one text element, shows the why whole, and gives a row's spare height to the space above the metadata", async () => {
    const shown = pickOf("sql-analyst", {
      title: "A SQL analyst",
      demoExcerpt: DEMO,
    });
    const plain = pickOf("triage-helper", { title: "A triage helper" });
    const listedEntry = card("notes-plugin", { title: "A notes plugin" });
    serve({ results: [shown, plain, listedEntry], picks: [shown, plain] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    const grid = await screen.findByTestId("hub-entry-grid");
    const pickCards = within(start).getAllByTestId("hub-entry-card");
    const [demoPick, plainPick] = pickCards;
    const listed = within(grid).getByTestId("hub-entry-card");
    expectWholeLines(within(listed).getByTestId("hub-entry-card-tagline"), 2);
    expectWholeLines(within(demoPick).getByTestId("hub-entry-card-tagline"), 2);
    expect(flexOf(within(demoPick).getByTestId("hub-entry-card-why"))).toEqual({
      grow: 0,
      shrink: 0,
    });
    expect(flexOf(within(plainPick).getByTestId("hub-entry-card-why"))).toEqual(
      { grow: 0, shrink: 0 },
    );
    for (const pick of [demoPick, plainPick]) {
      const why = within(pick).getByTestId("hub-entry-card-why");
      expect(declared(why, "-webkit-line-clamp")).toBe("");
    }
    for (const entryCard of [demoPick, plainPick, listed]) {
      const title = within(entryCard).getByTestId("hub-entry-card-title");
      expect(declared(title, "-webkit-line-clamp")).toBe("2");
      expect(flexOf(title).grow).toBe(0);
    }
    // A card taller than its text (its row holds a longer one) keeps the
    // extra height above its metadata, so the feet of a row line up.
    const meta = within(listed).getByTestId("hub-entry-card-meta");
    expect(declared(meta, "margin-top")).toBe("auto");
  });

  it("shows a listed pick's Maintainer pick line whole among search results", async () => {
    const picked = pickOf("sql-analyst", { title: "A SQL analyst" });
    serve({
      entries: ({ config }) =>
        page(config?.params?.q ? [picked] : [card("notes-plugin")]),
      picks: [picked],
    });
    openBrowse("/dashboard/hub?q=sql");
    const entryCard = await screen.findByTestId("hub-entry-card");
    const why = within(entryCard).getByTestId("hub-entry-card-why");
    expect(why).toHaveTextContent(`Maintainer pick: ${WHY}`);
    expect(flexOf(why)).toEqual({ grow: 0, shrink: 0 });
    expect(declared(why, "-webkit-line-clamp")).toBe("");
    const tagline = within(entryCard).getByTestId("hub-entry-card-tagline");
    expectWholeLines(tagline, 2);
  });

  it("evens out a card title's lines, and keeps a number a hyphen joins to a word on that word's line, so no line starts with '-9'", async () => {
    const titles = [
      {
        name: "engineering-team",
        title: "An engineering team that survives a kill -9",
        joined: "kill -9",
      },
      {
        name: "model-helper",
        title: "A helper that drafts with GPT-4o and checks it",
        joined: "GPT-4o",
      },
    ];
    serve({ results: titles.map(({ name, title }) => card(name, { title })) });
    openBrowse();
    const cards = await screen.findAllByTestId("hub-entry-card");
    expect(cards).toHaveLength(titles.length);
    titles.forEach(({ title: text, joined }, index) => {
      const title = within(cards[index]).getByTestId("hub-entry-card-title");
      // A browser that can evens the lines out, so the last word does not
      // sit alone; one that cannot wraps as before.
      expect(declared(title, "text-wrap")).toBe("pretty");
      // The title reads, and is named, exactly as written.
      const link = within(title).getByRole("link", { name: text });
      expect(link.textContent).toBe(text);
      const kept = [...link.querySelectorAll("span")].find(
        (span) => span.textContent === joined,
      );
      expect(kept).toBeDefined();
      expect(kept.children).toHaveLength(0);
      expect(getComputedStyle(kept).whiteSpace).toBe("nowrap");
    });
  });
});

describe("Hub home: a pick with no recorded demo says what it does", () => {
  // The Hub page's own width at 1440, 1280 and 1024 with the sidebar, and on
  // a phone.
  const WIDTHS = [1150, 990, 776];
  const PHONE = 360;

  it("fills the demo's place with What it does: its tagline in whole lines and its tags as the entry page's chips, in a block as tall as its text, and takes its row's height beside a pick with a demo, its spare height above its metadata", async () => {
    const shown = pickOf("sql-analyst", {
      title: "A SQL analyst",
      demoExcerpt: DEMO,
    });
    const plain = pickOf("triage-helper", {
      title: "A triage helper",
      demoExcerpt: null,
      tags: ["support", "triage", "email"],
    });
    serve({ results: [shown, plain, card("c")], picks: [shown, plain] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    const pickCards = within(start).getAllByTestId("hub-entry-card");
    const [demoPick, plainPick] = pickCards;
    expect(plainPick).toHaveAttribute("data-ref", "future-agi/triage-helper");
    const about = within(plainPick).getByTestId("hub-entry-card-about");
    expect(about.firstElementChild).toHaveTextContent(/^What it does$/);
    const said = within(about).getByTestId("hub-entry-card-about-text");
    expect(said).toHaveTextContent(/^What triage-helper does\.$/);
    expectWholeLines(said, 4);
    const tags = within(about).getByRole("list", { name: "Tags" });
    const chips = within(tags).getAllByRole("listitem");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "support",
      "triage",
      "email",
    ]);
    // Each tag is the chip the entry page draws for the same tag (small,
    // outlined), not a pill of the card's own.
    for (const chip of chips) {
      expect(chip).toHaveClass(
        "MuiChip-root",
        "MuiChip-sizeSmall",
        "MuiChip-outlined",
      );
    }
    // The maintainer's why leads, then the block; what it does is said once.
    const why = within(plainPick).getByTestId("hub-entry-card-why");
    expect(why.nextElementSibling).toBe(about);
    expect(
      within(plainPick).queryByTestId("hub-entry-card-tagline"),
    ).toBeNull();
    const times = plainPick.textContent.split("What triage-helper does.");
    expect(times).toHaveLength(2);
    const { text } = palette("light");
    expect(getComputedStyle(why).color).toBe(cssRgb(text.primary));
    expect(getComputedStyle(said).color).toBe(cssRgb(text.secondary));
    // The block is as tall as its text and tags, with no empty block under
    // the tags. The card takes its row's height beside the pick with a demo
    // (the two share one row at 1440, 1280 and 1024; on a phone each has a
    // row of its own), and its spare height sits above its metadata.
    expect(flexOf(about).grow).toBe(0);
    expect(flexOf(tags.parentElement).grow).toBe(0);
    expect(tags.parentElement).toBe(said.parentElement);
    const meta = within(plainPick).getByTestId("hub-entry-card-meta");
    expect(declared(meta, "margin-top")).toBe("auto");
    for (const width of [...WIDTHS, PHONE]) {
      expect(alignAt(plainPick, width)).toBe("");
    }
    expect(plainPick.lastElementChild).toBe(
      within(plainPick).getByTestId("hub-entry-card-foot"),
    );
    // The pick with a demo, and every card of More entries, take their row's
    // height too.
    const listed = within(screen.getByTestId("hub-entry-grid")).getByTestId(
      "hub-entry-card",
    );
    for (const width of [...WIDTHS, PHONE]) {
      expect(alignAt(demoPick, width)).toBe("");
      expect(alignAt(listed, width)).toBe("");
    }
    // A pick with a demo keeps its tagline and excerpt instead.
    expect(within(demoPick).queryByTestId("hub-entry-card-about")).toBeNull();
    expect(
      within(demoPick).getByTestId("hub-entry-card-demo"),
    ).toBeInTheDocument();
  });

  it("says the entry's description there when the pick carries one, in place of its tagline", async () => {
    const description =
      "Reads each new ticket, files it under billing, bugs or how-to, and drafts a first answer for a person to send.";
    const plain = pickOf("triage-helper", { description });
    serve({ results: [plain, card("c")], picks: [plain] });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    const about = within(start).getByTestId("hub-entry-card-about");
    expect(
      within(about).getByTestId("hub-entry-card-about-text"),
    ).toHaveTextContent(description);
    expect(start).not.toHaveTextContent("What triage-helper does.");
  });

  it("puts the picks with a recorded demo first in Start here, each group in the maintainers' order", async () => {
    const picks = [
      pickOf("plain-a", { demoExcerpt: null }),
      pickOf("demo-b", { demoExcerpt: DEMO }),
      pickOf("plain-c"),
      pickOf("demo-d", { demoExcerpt: DEMO }),
    ];
    serve({ results: [...picks, card("e")], picks });
    openBrowse();
    expect(refsIn(await screen.findByTestId("hub-picks-grid"))).toEqual([
      "future-agi/demo-b",
      "future-agi/demo-d",
      "future-agi/plain-a",
      "future-agi/plain-c",
    ]);
  });
});

describe("Hub home: a pick with no recorded demo shows how it runs", () => {
  // The Hub page's own width at 1440, 1280 and 1024 with the sidebar, at
  // both widths measured for each.
  const WIDTHS = [1150, 1192, 990, 1032, 734, 776];
  // The approved version the pick with no demo names. Its run command and
  // keys differ from the entry fixture's, so only this version supplies them.
  const VERSION = `${ENTRIES}/future-agi/triage-helper/versions/1.0.0`;
  const MANIFEST = {
    run: { keyless: ["node", "--import", "tsx", "triage.ts"] },
    keys: [
      { name: "ZENDESK_API_TOKEN", why: "Reads the tickets.", for: "live" },
      { name: "OPENAI_API_KEY", why: "Drafts the answers.", for: "live" },
    ],
    // Its one live model reads both keys, so a live run needs both.
    models: {
      live: [
        {
          model: "openai:gpt-4o-mini",
          keys: ["ZENDESK_API_TOKEN", "OPENAI_API_KEY"],
        },
      ],
    },
  };
  const COMMAND = "node --import tsx triage.ts";
  const COPY = "Copy the run command of A triage helper";

  const versionReads = () =>
    calls
      .filter((call) => call.path.includes("/versions/"))
      .map((call) => call.path);

  // The command a run block shows, as text.
  function commandIn(node) {
    const command = within(node).getByTestId("hub-entry-card-run-command");
    return command.querySelector("code").textContent;
  }

  // A pick with a demo beside one without, sharing one row at 1440, 1280 and
  // 1024. With no command line released (`parts`, none by default), each
  // foot holds Get the files. `plainFields` adds to the pick with no demo.
  async function openPicks(
    version,
    status = 200,
    { parts = [], plainFields = {} } = {},
  ) {
    const shown = pickOf("sql-analyst", {
      title: "A SQL analyst",
      demoExcerpt: DEMO,
    });
    const plain = pickOf("triage-helper", {
      title: "A triage helper",
      demoExcerpt: null,
      tags: ["support"],
      ...plainFields,
    });
    serve({
      parts,
      results: [shown, plain, card("c")],
      picks: [shown, plain],
      extra: [["get", VERSION, version, status]],
    });
    openBrowse();
    const start = await screen.findByTestId("hub-picks");
    return within(start).getAllByTestId("hub-entry-card");
  }

  it("puts its own run command, with copy, and the keys a live run reads under What it does, from its approved version", async () => {
    const [demoPick, plainPick] = await openPicks({
      version: "1.0.0",
      manifest: MANIFEST,
    });
    expect(plainPick).toHaveAttribute("data-ref", "future-agi/triage-helper");
    const runs = await within(plainPick).findByTestId("hub-entry-card-run");
    // The command runs inside the entry's files, and the caption says so.
    expect(runs.firstElementChild).toHaveTextContent(
      /^In its files, run it without a key$/,
    );
    const command = within(runs).getByTestId("hub-entry-card-run-command");
    expect(command.querySelector("code").textContent).toBe(COMMAND);
    fireEvent.click(within(runs).getByRole("button", { name: COPY }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(COMMAND));
    const keys = within(runs).getByTestId("hub-entry-card-run-keys");
    expect(keys).toHaveTextContent(
      /^For a live run, it reads: ZENDESK_API_TOKEN, OPENAI_API_KEY$/,
    );
    expect(
      [...keys.querySelectorAll("code")].map((name) => name.textContent),
    ).toEqual(["ZENDESK_API_TOKEN", "OPENAI_API_KEY"]);
    // The why, What it does, how it runs, then the metadata and the foot.
    const about = within(plainPick).getByTestId("hub-entry-card-about");
    const meta = within(plainPick).getByTestId("hub-entry-card-meta");
    expect(about.nextElementSibling).toBe(runs);
    expect(runs.nextElementSibling).toBe(meta);
    // Read from that one version; no other card reads one, and the pick with
    // a demo and the cards of More entries show no run block.
    expect(new Set(versionReads())).toEqual(new Set([VERSION]));
    expect(within(demoPick).queryByTestId("hub-entry-card-run")).toBeNull();
    const listed = within(screen.getByTestId("hub-entry-grid")).getByTestId(
      "hub-entry-card",
    );
    expect(within(listed).queryByTestId("hub-entry-card-run")).toBeNull();
  });

  it("takes its row's height beside the pick with a demo at 1440, 1280 and 1024 with the sidebar, so both end on one line with Get the files in their feet, and no block stretches into an empty box", async () => {
    const [demoPick, plainPick] = await openPicks({
      version: "1.0.0",
      manifest: MANIFEST,
    });
    const runs = await within(plainPick).findByTestId("hub-entry-card-run");
    for (const width of WIDTHS) {
      expect(alignAt(demoPick, width)).toBe("");
      expect(alignAt(plainPick, width)).toBe("");
    }
    for (const pick of [demoPick, plainPick]) {
      const foot = within(pick).getByTestId("hub-entry-card-foot");
      expect(pick.lastElementChild).toBe(foot);
      expect(
        within(foot).getByRole("link", {
          name: `Get the files of ${titleOf(pick)}`,
        }),
      ).toBeInTheDocument();
    }
    // The row's spare height goes above the metadata, as on every card.
    const meta = within(plainPick).getByTestId("hub-entry-card-meta");
    expect(declared(meta, "margin-top")).toBe("auto");
    expect(flexOf(runs).grow).toBe(0);
    expect(
      flexOf(within(plainPick).getByTestId("hub-entry-card-about")).grow,
    ).toBe(0);
    // The pick with a demo keeps its excerpt.
    expect(
      within(demoPick).getByTestId("hub-entry-card-demo"),
    ).toBeInTheDocument();
  });

  it("keeps the demo pick's code box as tall as its lines beside the taller pick at 1440, 1280 and 1024, so it never stretches into an empty box", async () => {
    const [demoPick, plainPick] = await openPicks({
      version: "1.0.0",
      manifest: MANIFEST,
    });
    await within(plainPick).findByTestId("hub-entry-card-run");
    const [line] = within(demoPick).getAllByTestId("hub-entry-card-demo-line");
    const box = line.parentElement;
    expect(box.tagName).toBe("PRE");
    // Its lines set its height: it takes none of the card's spare height
    // and gives up none of its own.
    expect(flexOf(box)).toEqual({ grow: 0, shrink: 0 });
    // The row's other rules hold: both picks take the row's height, each
    // keeps its spare height above its metadata, and both end on their feet.
    for (const width of WIDTHS) {
      expect(alignAt(demoPick, width)).toBe("");
      expect(alignAt(plainPick, width)).toBe("");
    }
    for (const pick of [demoPick, plainPick]) {
      const meta = within(pick).getByTestId("hub-entry-card-meta");
      expect(declared(meta, "margin-top")).toBe("auto");
      expect(pick.lastElementChild).toBe(
        within(pick).getByTestId("hub-entry-card-foot"),
      );
    }
  });

  it("shows the run command alone when a live run reads no key, quoting an argument with a space", async () => {
    const [, plainPick] = await openPicks({
      version: "1.0.0",
      manifest: {
        run: { keyless: ["python", "-m", "triage", "--label", "first answer"] },
        keys: [],
      },
    });
    const runs = await within(plainPick).findByTestId("hub-entry-card-run");
    const command = within(runs).getByTestId("hub-entry-card-run-command");
    expect(command.querySelector("code").textContent).toBe(
      "python -m triage --label 'first answer'",
    );
    expect(within(runs).queryByTestId("hub-entry-card-run-keys")).toBeNull();
  });

  it("says any one of the keys is enough when each live model reads one key, as the entry page's Keys panel says it", async () => {
    // The official entry's shape: three live models, one vendor's key each.
    const { keys, models } = official.latest.manifest;
    const [, plainPick] = await openPicks({
      version: "1.0.0",
      manifest: { ...MANIFEST, keys, models },
    });
    const runs = await within(plainPick).findByTestId("hub-entry-card-run");
    const line = within(runs).getByTestId("hub-entry-card-run-keys");
    expect(line).toHaveTextContent(
      /^For a live run, any one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, OMEGA_VERTEX_CREDS$/,
    );
    expect(
      [...line.querySelectorAll("code")].map((name) => name.textContent),
    ).toEqual(["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OMEGA_VERTEX_CREDS"]);
  });

  it("for a pick that runs without a key, says where its command runs and says 'without a key' once, in its foot", async () => {
    const [, plainPick] = await openPicks(
      { version: "1.0.0", manifest: { run: MANIFEST.run, keys: [] } },
      200,
      { plainFields: { noKeyNeeded: true } },
    );
    const runs = await within(plainPick).findByTestId("hub-entry-card-run");
    expect(runs.firstElementChild).toHaveTextContent(/^In its files, run it$/);
    expect(commandIn(runs)).toBe(COMMAND);
    expect(plainPick.textContent.match(/without a key/g)).toHaveLength(1);
    expect(
      within(plainPick).getByTestId("hub-entry-card-foot"),
    ).toHaveTextContent("Runs without a key");
  });

  it("with the command line released, shows the run command its Use offers in place of the one in its files, so the card shows one way to run it", async () => {
    const run = runOf("triage-helper");
    const [, plainPick] = await openPicks(
      { version: "1.0.0", manifest: MANIFEST },
      200,
      { parts: CLI },
    );
    const runs = await within(plainPick).findByTestId("hub-entry-card-run");
    await waitFor(() => expect(commandIn(runs)).toBe(run));
    expect(runs.firstElementChild).toHaveTextContent(/^Run it$/);
    expect(plainPick).not.toHaveTextContent(COMMAND);
    expect(plainPick).not.toHaveTextContent("In its files");
    fireEvent.click(within(runs).getByRole("button", { name: COPY }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(run));
    // The keys a live run reads stay under it.
    expect(
      within(runs).getByTestId("hub-entry-card-run-keys"),
    ).toHaveTextContent(/^For a live run, it reads: /);
    // Its foot holds Use, whose run command is the same one.
    expect(within(plainPick).queryByTestId("hub-entry-get-files")).toBeNull();
    fireEvent.click(
      within(plainPick).getByRole("button", { name: "Use A triage helper" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Use A triage helper",
    });
    const used = within(dialog).getByTestId("hub-entry-use-run");
    expect(used.querySelector("code").textContent).toBe(run);
  });

  it("holds the place with a placeholder of its shape while the version loads, then draws nothing when the version names no run command and no keys", async () => {
    const version = held();
    const [, plainPick] = await openPicks(() => version.answer);
    const loading = await within(plainPick).findByTestId(
      "hub-entry-card-run-loading",
    );
    expect(loading).toHaveAttribute("aria-hidden", "true");
    version.release({ version: "1.0.0", manifest: {} });
    await waitFor(() =>
      expect(
        within(plainPick).queryByTestId("hub-entry-card-run-loading"),
      ).toBeNull(),
    );
    expect(within(plainPick).queryByTestId("hub-entry-card-run")).toBeNull();
    expect(plainPick).not.toHaveTextContent(/run it/i);
    expect(plainPick).not.toHaveTextContent("For a live run");
    for (const width of WIDTHS) expect(alignAt(plainPick, width)).toBe("");
  });

  it("draws nothing in its place when the version does not load", async () => {
    const [, plainPick] = await openPicks(MISSING, 404);
    await waitFor(() => expect(versionReads()).toEqual([VERSION]));
    await settle();
    expect(within(plainPick).queryByTestId("hub-entry-card-run")).toBeNull();
    expect(
      within(plainPick).queryByTestId("hub-entry-card-run-loading"),
    ).toBeNull();
    expect(
      within(plainPick).getByTestId("hub-entry-card-about"),
    ).toBeInTheDocument();
  });
});

describe("Hub home: the entry to start with has a card's action", () => {
  it("with no command line, its title opens the entry and Get the files downloads its approved files, as on a card", async () => {
    serve({ parts: [], results: [card("notes-plugin")] });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    await settle();
    expect(
      within(box).getByRole("heading", { name: "Start with this one" }),
    ).toBeInTheDocument();
    expect(
      within(box).getByRole("link", { name: official.title }),
    ).toHaveAttribute("href", "/dashboard/hub/future-agi/first-agent");
    const files = within(box).getByRole("link", {
      name: `Get the files of ${official.title}`,
    });
    expect(files).toHaveTextContent(/^Get the files$/);
    expect(files).toHaveAttribute("href", bundleOf("first-agent"));
    expect(files).toHaveAttribute("target", "_blank");
    expect(files).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(box).queryByTestId("hub-entry-use")).toBeNull();
  });

  it("with the command line released, Use opens the entry's run and add commands, as on a card", async () => {
    serve({ results: [card("notes-plugin")] });
    openBrowse();
    const box = await screen.findByTestId("hub-try-one");
    const use = await within(box).findByRole("button", {
      name: `Use ${official.title}`,
    });
    fireEvent.click(use);
    const dialog = await screen.findByRole("dialog", {
      name: `Use ${official.title}`,
    });
    const run = within(dialog).getByTestId("hub-entry-use-run");
    const add = within(dialog).getByTestId("hub-entry-use-add");
    expect(run.querySelector("code").textContent).toBe(RUN);
    expect(add.querySelector("code").textContent).toBe(addOf("first-agent"));
    expect(within(box).queryByTestId("hub-entry-get-files")).toBeNull();
  });
});
