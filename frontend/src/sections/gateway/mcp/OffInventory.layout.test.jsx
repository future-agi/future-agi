import React from "react";
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";

import { SavedOverview } from "./OffInventory";
import { renderMcp } from "./testing/renderMcp";

vi.mock("../hooks/useGatewayAdmin", () => ({
  useGatewayAdmin: () => ({ canEdit: true }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useGatewayConfig: () => ({
    data: { mcp: { servers: { alpha: {} } } },
    isLoading: false,
    isError: false,
  }),
}));

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
// computed value for this negative margin, so the rule text is read instead.
function ownRules(el) {
  const css = allCss();
  const classes = Array.from(el.classList).filter((c) => c.startsWith("css-"));
  const rule = (c) => css.match(new RegExp(`\\.${c}\\s*\\{[^}]*\\}`, "g"));
  return classes.flatMap((c) => rule(c) || []).join("\n");
}

// With MCP off, the "All servers" text button's label sat
// 8 px right of the content edge, a stray heading rather than a link. Its box
// takes back the button's own 8 px padding so the label lines up.
describe("MCP > Overview with MCP off (layout)", () => {
  it("lines the All servers label up with the content edge", () => {
    renderMcp(<SavedOverview gatewayId="gw-1" />);
    const button = screen.getByRole("button", { name: "All servers" });
    expect(ownRules(button.parentElement)).toMatch(/margin-left:\s*-8px/);
  });
});
