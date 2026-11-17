import React from "react";
import PropTypes from "prop-types";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { button } from "src/theme/overrides/components/button";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import rules from "../__fixtures__/rules.json";
import official from "../__fixtures__/entry-official.json";
import versions from "../__fixtures__/versions.json";
import meUser from "../__fixtures__/me-user.json";
import HubEntryPage from "../HubEntryPage";
import { EntryAction } from "../components/EntryCommands";
import EvidencePanel from "../components/EvidencePanel";
import UseThisMenu from "../components/UseThisMenu";
import { respond } from "./mockAxios";

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/config-global", () => ({ HOST_API: "http://localhost:8000" }));

// A Hub button with a mark after its words (a chevron that opens a menu or
// a part of the page) draws that mark the theme's icon gap away from them:
// the 6 px the theme leaves after a start icon, from its spacing scale, and
// nothing after the mark. The theme's own button styles space icons for one
// at the start (no space before it), so each of these buttons sets its own
// gap; the theme is left as it is. Each case renders the button with the
// app's button styles and reads the gap as a browser cascades it.

// The app's theme with its button styles, built the way src/theme builds it.
function appTheme(mode = "light") {
  const theme = createTheme({ palette: palette(mode), typography });
  theme.components = button(theme);
  return theme;
}

const GAP = appTheme().spacing(0.75);

// Renders `element` at `path` visited as `at`, with the providers a Hub page
// needs and the app's button styles.
function renderWithButtons(element, { path = "*", at = "/" } = {}) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const theme = appTheme();
  function Wrapper({ children }) {
    return (
      <HelmetProvider>
        <QueryClientProvider client={client}>
          <ThemeProvider theme={theme}>
            <MemoryRouter initialEntries={[at]}>
              <Routes>
                <Route path={path} element={children} />
              </Routes>
            </MemoryRouter>
          </ThemeProvider>
        </QueryClientProvider>
      </HelmetProvider>
    );
  }
  Wrapper.propTypes = { children: PropTypes.node };
  return render(element, { wrapper: Wrapper });
}

// A selector jsdom cannot match (a pseudo-element) never applies here.
function matches(node, selector) {
  try {
    return node.matches(selector);
  } catch {
    return false;
  }
}

const count = (text, pattern) => (text.match(pattern) || []).length;

// How specific a selector is, as one number that orders as a browser does:
// ids, then classes, attributes and pseudo-classes, then element names.
function specificity(selector) {
  const ids = count(selector, /#[\w-]+/g);
  const classes = count(selector, /\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+/g);
  const names = count(selector, /(^|[\s>+~])[a-z][\w-]*/gi);
  return ids * 10000 + classes * 100 + names;
}

// The value the page's style rules give `node` for `property`, as a browser
// cascades them: the most specific matching rule wins, and of two as
// specific, the later one.
function cascaded(node, property) {
  let best = null;
  for (const sheet of Array.from(document.styleSheets)) {
    for (const rule of Array.from(sheet.cssRules || [])) {
      const value = rule.style?.getPropertyValue(property);
      if (rule.selectorText && value) {
        const weight = Math.max(
          ...rule.selectorText
            .split(",")
            .map((part) => part.trim())
            .map((part) => (matches(node, part) ? specificity(part) : -1)),
        );
        if (weight >= 0 && (!best || weight >= best.weight))
          best = { weight, value };
      }
    }
  }
  return best ? best.value : "";
}

// The mark after `button`'s words sits the theme's icon gap away from them,
// and nothing trails it.
function expectGapBeforeEndIcon(button) {
  const end = button.querySelector(".MuiButton-endIcon");
  expect(end).not.toBeNull();
  expect(cascaded(end, "margin-left")).toBe(GAP);
  expect(cascaded(end, "margin-right")).toMatch(/^0(px)?$/);
}

const ENTRY = "/omega-hub/v1/entries/future-agi/first-agent";
const CAPABILITIES = "/api/capabilities/";
const CLI = capabilitiesAnswer({ parts: [OMEGA_PART.HUB_CLI] });

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => {
  respond([]);
  vi.unstubAllEnvs();
});

describe("A mark after a Hub button's words keeps a gap from them", () => {
  it("the theme's own gap is a real one, so a gap equal to it is never zero", () => {
    expect(GAP).toMatch(/^[1-9]\d*px$/);
  });

  it("Use on a card: its chevron", async () => {
    respond([["get", CAPABILITIES, CLI]]);
    renderWithButtons(
      <EntryAction
        entry={{
          ref: "future-agi/first-agent",
          name: "first-agent",
          title: "Your first agent in one file",
          latestVersion: "1.0.0",
          publisher: { handle: "future-agi" },
        }}
      />,
    );
    const use = await screen.findByTestId("hub-entry-use");
    expect(use).toHaveTextContent("Use");
    expectGapBeforeEndIcon(use);
  });

  it("Open in Future AGI, under Use this on the entry page: its chevron", async () => {
    respond([["get", CAPABILITIES, CLI]]);
    renderWithButtons(
      <UseThisMenu
        entryRef="future-agi/first-agent"
        version="1.0.0"
        capabilities={{
          cli: true,
          openInStudio: true,
          deploy: true,
          consoleTemplate: true,
        }}
        flags={{ openInStudio: true, deploy: true, consoleTemplate: true }}
      />,
    );
    const open = await screen.findByTestId("hub-use-this-button");
    expect(open).toHaveTextContent("Open in Future AGI");
    expectGapBeforeEndIcon(open);
  });

  it("More, under the entry's title, and the Provenance toggles: their chevrons", async () => {
    respond([
      ["get", CAPABILITIES, capabilitiesAnswer({ parts: [] })],
      ["get", ENTRY, structuredClone(official)],
      ["get", `${ENTRY}/versions`, versions],
      ["get", `${ENTRY}/remixes`, []],
      ["get", "/omega-hub/v1/rules", rules],
      ["get", "/omega-hub/v1/me", meUser],
      [
        "get",
        /^\/omega-hub\/v1\/publishers\//,
        { ...official.publisher, entryCount: 3 },
      ],
    ]);
    renderWithButtons(<HubEntryPage />, {
      path: "/dashboard/hub/:publisher/:name/:tab?",
      at: "/dashboard/hub/future-agi/first-agent",
    });
    const more = await screen.findByTestId("hub-more-button");
    expect(more).toHaveTextContent("More");
    expectGapBeforeEndIcon(more);
    const provenance = await screen.findByRole("region", {
      name: "Provenance",
    });
    expectGapBeforeEndIcon(
      within(provenance).getByRole("button", { name: "Hide the checks" }),
    );
    expectGapBeforeEndIcon(
      await within(provenance).findByRole("button", {
        name: "What review means",
      }),
    );
  });

  it("What does this mean?, under Evidence: its chevron", () => {
    respond([["get", "/omega-hub/v1/rules", rules]]);
    renderWithButtons(
      <EvidencePanel version={official.latest} tier="official" />,
    );
    expectGapBeforeEndIcon(
      screen.getByRole("button", { name: "What does this mean?" }),
    );
  });
});
