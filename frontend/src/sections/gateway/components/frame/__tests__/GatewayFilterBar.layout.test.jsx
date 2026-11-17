import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import Button from "@mui/material/Button";
import { ThemeProvider, createTheme } from "@mui/material/styles";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { GatewayFilterBar } from "..";
import FilterSelect from "../../../mcp/components/FilterSelect";

const renderBar = () =>
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <GatewayFilterBar
        search=""
        onSearchChange={() => {}}
        searchPlaceholder="Search servers"
        actions={<Button size="small">Add server</Button>}
      >
        <FilterSelect
          label="Status"
          value=""
          onChange={() => {}}
          options={[]}
        />
      </GatewayFilterBar>
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

// The page's CSS split in two: the text inside every
// "@supports not (align-items: last baseline)" block, and the rest.
const FALLBACK = /@supports\s+not\s*\(align-items:\s*last baseline\)\s*\{/g;

function splitFallback(css) {
  const blocks = [];
  let rest = "";
  let from = 0;
  for (const match of css.matchAll(FALLBACK)) {
    if (match.index < from) continue;
    let depth = 1;
    let i = match.index + match[0].length;
    while (i < css.length && depth > 0) {
      if (css[i] === "{") depth += 1;
      if (css[i] === "}") depth -= 1;
      i += 1;
    }
    rest += css.slice(from, match.index);
    blocks.push(css.slice(match.index + match[0].length, i - 1));
    from = i;
  }
  return { blocks: blocks.join("\n"), rest: rest + css.slice(from) };
}

// The rules of this element's own emotion classes. jsdom 26 has no layout
// and may drop a value it does not know, so the rule text is read instead.
function rulesFor(el, css) {
  const classes = Array.from(el.classList).filter((c) => c.startsWith("css-"));
  const rule = (c) => css.match(new RegExp(`\\.${c}\\s*\\{[^}]*\\}`, "g"));
  return classes.flatMap((c) => rule(c) || []).join("\n");
}

// Its rules for browsers with "last baseline", and its fallback rules.
const ownRules = (el) => rulesFor(el, splitFallback(allCss()).rest);
const fallbackRules = (el) => rulesFor(el, splitFallback(allCss()).blocks);

const searchBox = () =>
  screen
    .getByRole("textbox", { name: "Search servers" })
    .closest(".MuiFormControl-root");
const actionsBox = () =>
  screen.getByRole("button", { name: "Add server" }).parentElement;

// A labelled select is its label above its field, so a centred row counted
// the label: plain children, chips and buttons sat off the field's centre
// line. The row lines every control up on the fields' text (last) baseline,
// and no control is pinned to the bottom edge on its own. A browser without
// "last baseline" would drop the value and stretch every control, so there
// the row keeps the earlier rule: centred, with the search box and the
// actions on the fields' bottom edge (the actions in a 30 px band).
describe("GatewayFilterBar (layout)", () => {
  it("lines its controls up on the fields' text baseline", () => {
    renderBar();
    expect(ownRules(screen.getByRole("search"))).toMatch(
      /align-items:\s*last baseline/,
    );
  });

  it("leaves the search box and the actions on the row's baseline", () => {
    renderBar();
    const input = screen.getByRole("textbox", { name: "Search servers" });
    const search = input.closest(".MuiFormControl-root");
    expect(window.getComputedStyle(search).alignSelf).not.toBe("flex-end");
    const button = screen.getByRole("button", { name: "Add server" });
    const style = window.getComputedStyle(button.parentElement);
    expect(style.alignSelf).not.toBe("flex-end");
    expect(style.alignItems).toBe("center");
    expect(ownRules(searchBox())).not.toMatch(/align-self:\s*flex-end/);
    expect(ownRules(actionsBox())).not.toMatch(/align-self:\s*flex-end/);
    expect(ownRules(actionsBox())).not.toMatch(/min-height:\s*30px/);
  });

  it("centres the row where last baseline is not supported", () => {
    renderBar();
    expect(fallbackRules(screen.getByRole("search"))).toMatch(
      /align-items:\s*center/,
    );
  });

  it("puts the search box and the actions on the bottom edge there", () => {
    renderBar();
    expect(fallbackRules(searchBox())).toMatch(/align-self:\s*flex-end/);
    const actions = fallbackRules(actionsBox());
    expect(actions).toMatch(/align-self:\s*flex-end/);
    expect(actions).toMatch(/min-height:\s*30px/);
  });
});
