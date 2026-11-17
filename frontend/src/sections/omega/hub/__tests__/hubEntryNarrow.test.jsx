import React from "react";
import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubEntryPage from "../HubEntryPage";
import axios, { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// The entry page below the lg breakpoint (a 1024 window with the app's
// sidebar open, a tablet). The side column's cards used to sit two to a row
// there, a short card beside a tall one with a gap under it, and the
// Readme, Files and Versions tabs came after every card, so a long entry's
// README started far down the page. Now the cards a visitor reads before
// running it (Keys, Disclosure) follow What it does, the tabs come next,
// and the cards about where it came from (Provenance, the publisher,
// Discord) close the page, each part a single stack of whole-width cards.
// On those cards the keys and the checks lie several to a row, each
// check's result right after its own name rather than at the far end of a
// wide cell, beside the next check's name.

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const AT = "/dashboard/hub/future-agi/first-agent";

function serve(entry = structuredClone(official)) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
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

const open = () =>
  renderAt(<HubEntryPage />, {
    path: "/dashboard/hub/:publisher/:name/:tab?",
    at: AT,
  });

// Narrower than the lg breakpoint. Returns the undo.
function narrowWindow() {
  const original = window.matchMedia.getMockImplementation();
  window.matchMedia.mockImplementation((query) => ({
    ...original(query),
    matches: /max-width/.test(query),
  }));
  return () => window.matchMedia.mockImplementation(original);
}

let restore = null;
beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => {
  restore?.();
  restore = null;
  vi.unstubAllEnvs();
});

// -1 when `a` comes before `b` in the page, else 1.
const before = (a, b) =>
  a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;

// The names of `parts` ([name, node] pairs) in the order the page holds
// them, which is the order a reader, a screen reader and Tab meet them.
const readingOrder = (parts) =>
  [...parts].sort(([, a], [, b]) => before(a, b)).map(([name]) => name);

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

describe("Entry page at a narrow width", () => {
  it("reads What it does, Keys and Disclosure, then the Readme, Files and Versions tabs, then Provenance, the publisher and Discord", async () => {
    restore = narrowWindow();
    serve();
    open();
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    const parts = [
      ["what it does", screen.getByTestId("hub-entry-what")],
      ["keys", screen.getByRole("region", { name: "Keys" })],
      ["disclosure", screen.getByRole("region", { name: "Disclosure" })],
      ["tabs", screen.getByRole("tablist", { name: "More about this entry" })],
      ["readme", screen.getByTestId("hub-readme")],
      ["provenance", provenance],
      ["publisher", screen.getByRole("region", { name: "Publisher" })],
      ["discord", screen.getByTestId("hub-discord-link")],
    ];
    expect(readingOrder(parts)).toEqual(parts.map(([name]) => name));
  });

  it("gives each side card the page's whole width, one card per row, with every card still there", async () => {
    restore = narrowWindow();
    serve();
    open();
    await screen.findByRole("region", { name: "Provenance" });
    const first = screen.getByRole("complementary", {
      name: "About this entry",
    });
    expect(getComputedStyle(first).display).toBe("flex");
    expect(getComputedStyle(first).flexDirection).toBe("column");
    for (const name of ["Keys", "Disclosure"]) {
      expect(first).toContainElement(screen.getByRole("region", { name }));
    }
    const rest = screen.getByRole("complementary", {
      name: "Provenance and publisher",
    });
    expect(getComputedStyle(rest).display).toBe("flex");
    expect(getComputedStyle(rest).flexDirection).toBe("column");
    for (const name of ["Provenance", "Publisher"]) {
      expect(rest).toContainElement(screen.getByRole("region", { name }));
    }
    expect(rest).toContainElement(screen.getByTestId("hub-discord-link"));
  });

  it("lets the key list and the check list fill a whole-width card, as many to a row as fit, each check's result right after its own name", async () => {
    restore = narrowWindow();
    serve();
    open();
    const keys = await screen.findByRole("region", { name: "Keys" });
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    const checks = within(provenance).getByRole("list", { name: "Checks" });
    const lists = [within(keys).getByRole("list"), checks];
    for (const list of lists) {
      expect(getComputedStyle(list).display).toBe("grid");
      expect(
        declared(list, "grid-template-columns").replace(/\s+/g, ""),
      ).toMatch(/^repeat\(auto-fill,minmax\(\d+px,1fr\)\)$/);
    }
    // Not pushed to the far end of a wide cell, beside the next check.
    for (const item of within(checks).getAllByRole("listitem")) {
      expect(getComputedStyle(item).justifyContent).toBe("flex-start");
    }
  });

  it("while the entry loads, the placeholders hold the same order: the first side cards, the tabs, then the rest", async () => {
    restore = narrowWindow();
    serve();
    const original = axios.get.getMockImplementation();
    const pending = vi
      .spyOn(axios, "get")
      .mockImplementation((url, config) =>
        String(url).split("?")[0] === ENTRY
          ? new Promise(() => {})
          : original(url, config),
      );
    try {
      open();
      const side = await screen.findByTestId("hub-loading-side");
      const tabs = screen.getByTestId("hub-loading-tabs");
      expect(before(side, tabs)).toBe(-1);
      const rest = screen.getByTestId("hub-loading-side-rest");
      const parts = [
        ["side", side],
        ["tabs", tabs],
        ["rest", rest],
      ];
      expect(readingOrder(parts)).toEqual(["side", "tabs", "rest"]);
    } finally {
      pending.mockImplementation(original);
    }
  });
});

describe("Entry page at a wide width", () => {
  it("keeps one side column beside the main one: Keys, Disclosure, Provenance, then the publisher, each check's result at the end of its row", async () => {
    serve();
    open();
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    const rail = screen.getByRole("complementary", {
      name: "About this entry",
    });
    expect(rail).toBe(screen.getByTestId("hub-entry-rail"));
    expect(rail.previousElementSibling).toBe(
      screen.getByTestId("hub-entry-main"),
    );
    const rest = screen.queryByRole("complementary", {
      name: "Provenance and publisher",
    });
    expect(rest).toBeNull();
    const order = [...rail.children]
      .map((node) => node.getAttribute("data-testid"))
      .filter(Boolean);
    expect(order).toEqual([
      "hub-keys",
      "hub-disclosure",
      "hub-provenance",
      "hub-publisher-card",
      "hub-discord-link",
    ]);
    // One check to a row in the side column, its result at the row's end.
    const checks = within(provenance).getByRole("list", { name: "Checks" });
    for (const item of within(checks).getAllByRole("listitem")) {
      expect(getComputedStyle(item).justifyContent).toBe("space-between");
    }
  });
});
