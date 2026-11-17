import React from "react";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { button } from "src/theme/overrides/components/button";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import DiscordLink from "../components/DiscordLink";

// "Discuss on Discord" as the app draws it, with the theme's own button
// styles: the theme spaces a button's icons for one at its start (no space
// before it, a gap after it). The mark after the words, which says the link
// leaves the app, sits that same gap away from them, never touching them.

// The app's theme with its button styles, built the way src/theme builds it.
function appTheme(mode) {
  const theme = createTheme({ palette: palette(mode), typography });
  theme.components = button(theme);
  return theme;
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
// specific, the later one. (The test DOM's own computed style orders rules by
// their place in the page alone, so it cannot tell a theme rule that wins on
// specificity from one that does not.)
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

describe("Discuss on Discord", () => {
  it.each([
    ["light", "text", true],
    ["dark", "outlined", false],
  ])(
    "keeps the theme's icon gap between its words and the external-link mark (%s, %s)",
    (mode, variant, fullWidth) => {
      render(
        <ThemeProvider theme={appTheme(mode)}>
          <DiscordLink variant={variant} fullWidth={fullWidth} />
        </ThemeProvider>,
      );
      const link = screen.getByTestId("hub-discord-link");
      expect(link).toHaveTextContent("Discuss on Discord");
      const start = link.querySelector(".MuiButton-startIcon");
      const end = link.querySelector(".MuiButton-endIcon");
      // The gap the theme leaves after a start icon...
      const gap = cascaded(start, "margin-right");
      expect(gap).toMatch(/^[1-9]\d*px$/);
      // ...is the gap before the mark at the end, and nothing trails it.
      expect(cascaded(end, "margin-left")).toBe(gap);
      expect(cascaded(end, "margin-right")).toMatch(/^0(px)?$/);
    },
  );
});
