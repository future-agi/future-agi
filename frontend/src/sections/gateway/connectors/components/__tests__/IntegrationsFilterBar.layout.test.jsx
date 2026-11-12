import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { toggleButton } from "src/theme/overrides/components/toggle-button";

import IntegrationsFilterBar from "../IntegrationsFilterBar";

// The app's segmented-control override (theme/overrides/components/
// toggle-button.js) draws a track with 2 px of padding around the buttons.
const base = createTheme({ palette: palette("light"), typography });
const theme = createTheme({
  palette: palette("light"),
  typography,
  components: toggleButton(base),
});

const renderBar = () =>
  render(
    <ThemeProvider theme={theme}>
      <IntegrationsFilterBar
        filters={{ view: "all", search: "", category: null, auth: null }}
        onChange={() => {}}
        connectedCount={0}
        authOptions={[
          { id: "api_key", label: "API key" },
          { id: "none", label: "No key" },
        ]}
      />
    </ThemeProvider>,
  );

// Every CSS rule in the page, as text: the <style> tags' text and the parsed
// sheets, so the check does not depend on how the styles were inserted.
const ruleText = (sheet) =>
  Array.from(sheet.cssRules || [], (rule) => rule.cssText);

const allCss = () => {
  const tags = Array.from(document.querySelectorAll("style"));
  const tagText = tags.map((tag) => tag.textContent);
  const sheets = Array.from(document.styleSheets);
  return [...tagText, ...sheets.flatMap(ruleText)].join("\n");
};

// The rules of this element's own emotion classes. jsdom 26 reports no
// computed value for a negative margin, so the rule text is read instead.
function ownRules(el) {
  const css = allCss();
  const classes = Array.from(el.classList).filter((c) => c.startsWith("css-"));
  const rule = (c) => css.match(new RegExp(`\\.${c}\\s*\\{[^}]*\\}`, "g"));
  return classes.flatMap((c) => rule(c) || []).join("\n");
}

// The gateway filter bar puts its search box on the row's bottom edge, where
// a labelled select's field sits. The view switch's track made the group
// 34 px, taller than the search box and the Auth select (32 px in the
// browser), so it set the row's height: the search box sat 1 px below the
// Auth select and off the buttons' centre line. The group takes its track
// padding back out of the row.
describe("Integrations filter bar (layout)", () => {
  it("keeps the view switch's track padding out of the row's height", () => {
    renderBar();
    const group = screen.getByRole("group", { name: "Show" });
    const css = ownRules(group);
    const padding = css.match(/padding:\s*(\d+)px/);
    expect(padding?.[1]).toBe("2");
    expect(css).toMatch(new RegExp(`margin-top:\\s*-${padding[1]}px`));
    expect(css).toMatch(new RegExp(`margin-bottom:\\s*-${padding[1]}px`));
  });
});
