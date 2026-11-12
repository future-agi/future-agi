import React from "react";
import { describe, it, expect } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { render, screen } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { palette } from "src/theme/palette";
import { defaultProps } from "src/theme/overrides/default-props";
import SpanDetailPane from "../SpanDetailPane";

// In the trace drawer's narrow span pane:
// - every tab label reads in full;
// - a card title ("User Message") stays on one line beside its
//   Markdown / Raw / YAML / Tree toggles;
// - the search box shrinks, so the Markdown | JSON toggle beside it stays
//   inside the pane.
// jsdom has no layout, so these tests pin the styles and structure the fix
// sets, never measured sizes.

// test-utils' theme plus the Tabs defaults the app runs with
// (theme/overrides/default-props.jsx): there every Tabs is "scrollable" with
// scroll buttons allowed on mobile, so a strip that should not scroll has to
// say so itself. test-utils' bare theme has no such defaults and would let a
// strip that leaves its variant to the theme pass here yet scroll in the app.
const appTabsTheme = (() => {
  const options = {
    palette: palette("light"),
    spacing: (factor) => `${0.25 * factor}rem`,
  };
  const { MuiTabs, MuiTab } = defaultProps(createTheme(options));
  return createTheme({ ...options, components: { MuiTabs, MuiTab } });
})();

// Every glyph the pane draws for this span, so no accessible name picks up
// "Missing offline icon".
for (const name of [
  "solar:document-text-linear",
  "solar:list-linear",
  "solar:check-circle-linear",
  "solar:pen-linear",
  "solar:bell-linear",
  "solar:copy-linear",
  "solar:alt-arrow-up-linear",
  "solar:alt-arrow-down-linear",
  "solar:alt-arrow-right-linear",
  "solar:chevrons-down-up-linear",
  "solar:chevrons-up-down-linear",
  "solar:magnifier-linear",
  "solar:tag-linear",
  "solar:add-linear",
  "solar:close-linear",
  "solar:arrow-left-down-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

const START = "2026-11-11T03:19:11.000Z";
const span = {
  id: "span-1",
  trace: "trace-1",
  name: "order-agent",
  start_time: START,
  latency_ms: 4000,
  input: "Where is order A-1001?",
  output: "Order A-1001 has shipped.",
};

// Inside the drawer the pane always gets the trace's spans, which adds the
// Log View tab: five tabs, the strip that overflowed at 1024 px.
const renderPane = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SpanDetailPane
        entry={{ observation_span: span }}
        allSpans={[{ observation_span: span, children: [] }]}
        traceStartTime={START}
        projectId="project-1"
        onClose={() => {}}
      />
    </QueryClientProvider>,
    { theme: appTabsTheme },
  );

const TAB_NAMES = ["Preview", "Log View", "Evals", "Annotations", "Events"];

const tabStrip = () =>
  screen.getByRole("tab", { name: "Annotations" }).closest(".MuiTabs-root");

describe("SpanDetailPane in a narrow (1024 px) drawer", () => {
  it("wraps the span tabs onto another row instead of scrolling them behind arrows", () => {
    // The render theme carries the app's default, so the strip below is
    // standard only because the pane sets it.
    expect(appTabsTheme.components.MuiTabs.defaultProps).toMatchObject({
      variant: "scrollable",
      allowScrollButtonsMobile: true,
    });

    renderPane();

    const tabs = tabStrip();
    expect(tabs).not.toBeNull();

    // A standard (non-scrolling) strip with no scroll arrows, against the
    // theme's scrollable default...
    const scroller = tabs.querySelector(".MuiTabs-scroller");
    expect(scroller).toHaveClass("MuiTabs-fixed");
    expect(scroller).not.toHaveClass("MuiTabs-scrollableX");
    expect(tabs.querySelector(".MuiTabs-scrollButtons")).toBeNull();
    // ...whose row of tabs wraps when the pane is too narrow for all five.
    expect(tabs.querySelector(".MuiTabs-flexContainer")).toHaveStyle({
      flexWrap: "wrap",
    });

    // Every tab stays in the strip; none is dropped to make room.
    for (const name of TAB_NAMES) {
      expect(tabs).toContainElement(screen.getByRole("tab", { name }));
    }
  });

  it("hides the one-row sliding indicator and underlines the selected tab itself", () => {
    renderPane();

    const tabs = tabStrip();
    expect(tabs.querySelector(".MuiTabs-indicator")).toHaveStyle({
      display: "none",
    });

    const selected = screen.getByRole("tab", { name: "Preview" });
    const other = screen.getByRole("tab", { name: "Annotations" });
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(window.getComputedStyle(selected).boxShadow).toMatch(/inset/);
    expect(window.getComputedStyle(other).boxShadow).not.toMatch(/inset/);
  });

  it("keeps a card title on one line and moves its view toggles below it as one group", () => {
    renderPane();

    const title = screen
      .getAllByText("Input")
      .find((el) => el.closest('[data-search-skip="true"]'));
    expect(title).toBeDefined();
    const header = title.closest('[data-search-skip="true"]');

    expect(header).toHaveStyle({ flexWrap: "wrap" });
    expect(title).toHaveStyle({ flexShrink: "0" });

    // Toggles, copy and chevron sit in one group that wraps as a unit and
    // stays right-aligned when it does.
    const markdown = header.querySelector('button[aria-pressed="true"]');
    expect(markdown).toHaveTextContent("Markdown");
    const group = markdown.parentElement.parentElement;
    expect(group.parentElement).toBe(header);
    expect(group).toContainElement(
      header.querySelector('button[aria-label="Copy"]'),
    );
    for (const label of ["Markdown", "Raw", "YAML", "Tree"]) {
      expect(group).toHaveTextContent(label);
    }
    expect(group).toHaveStyle({ flexShrink: "0", marginLeft: "auto" });
  });

  it("lets the search box shrink so the Markdown | JSON toggle stays inside the pane", () => {
    renderPane();

    const input = screen.getByPlaceholderText("Search");
    const searchBox = input.parentElement;
    expect(input).toHaveStyle({ minWidth: "0" });
    expect(searchBox).toHaveStyle({ minWidth: "0" });

    // The toggle is still beside the search box, whole.
    const json = screen.getByRole("tab", { name: "JSON" });
    const toggle = json.closest(".MuiTabs-root");
    expect(toggle).toContainElement(
      screen.getByRole("tab", { name: "Markdown" }),
    );
    expect(searchBox.parentElement).toContainElement(toggle);
  });
});
