// At 1024 px the Scenarios build-method strip
// (Workflow builder, Import datasets, Upload script, Call / Chat SOP) needs
// about 445 px in a 423 px column. As a scrollable strip it showed a cut
// "Uplo" and a scroll arrow, hiding the last two options. `wrap` lets a
// segmented strip that is too wide break onto a second line instead, with
// every option whole and no arrows; without it a strip is unchanged. jsdom
// lays nothing out: the pixel proof is a browser walk.
import React from "react";
import { render, screen } from "@testing-library/react";
import { Tab } from "@mui/material";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import { SegmentedTabs } from "../tabs";

// The app's theme, built as theme/index.jsx builds it: its Tabs default to
// the scrollable variant.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

// jsdom lays nothing out and its computed style drops properties it does not
// know, so read the element's own stylesheet rules (last declaration wins).
const declared = (element, property) => {
  let value = "";
  const visit = (rules) => {
    for (const rule of rules) {
      if (!rule.selectorText) {
        if (rule.cssRules) visit(rule.cssRules);
        continue;
      }
      let matches = false;
      try {
        matches = element.matches(rule.selectorText);
      } catch (error) {
        // Vendor pseudo-elements jsdom cannot parse never style these.
        if (error.name !== "SyntaxError") throw error;
      }
      const v = matches && rule.style.getPropertyValue(property);
      if (v) value = v.trim();
    }
  };
  for (const sheet of document.styleSheets) visit(sheet.cssRules);
  return value;
};

const LABELS = [
  "Workflow builder",
  "Import datasets",
  "Upload script",
  "Call / Chat SOP",
];

// setupTests stubs the observer as a no-op; report "last tab not fully in
// view", as at 1024 px.
let SavedObserver;
beforeEach(() => {
  SavedObserver = globalThis.IntersectionObserver;
  globalThis.IntersectionObserver = class {
    constructor(callback) {
      this.callback = callback;
    }

    observe() {
      this.callback([{ isIntersecting: false }]);
    }

    unobserve() {}

    disconnect() {}
  };
});
afterEach(() => {
  globalThis.IntersectionObserver = SavedObserver;
});

const renderStrip = (mode, props) =>
  render(
    <ThemeProvider theme={appTheme(mode)}>
      <SegmentedTabs value="graph" {...props}>
        {LABELS.map((label, i) => (
          <Tab key={label} value={i ? label : "graph"} label={label} />
        ))}
      </SegmentedTabs>
    </ThemeProvider>,
  );

describe("SegmentedTabs wrap", () => {
  it.each(["light", "dark"])(
    "breaks a strip that is too wide onto a second line, every option whole, no arrows (%s)",
    (mode) => {
      renderStrip(mode, { wrap: true });
      const strip = screen.getByRole("tablist");
      LABELS.forEach((name) =>
        expect(screen.getByRole("tab", { name })).toBeInTheDocument(),
      );
      const root = strip.closest(".MuiTabs-root");
      expect(root.querySelector(".MuiTabs-scrollButtons")).toBeNull();
      expect(declared(strip, "flex-wrap")).toBe("wrap");
      // The prop is the strip's own: it never reaches the DOM.
      expect(root).not.toHaveAttribute("wrap");
    },
  );
});
