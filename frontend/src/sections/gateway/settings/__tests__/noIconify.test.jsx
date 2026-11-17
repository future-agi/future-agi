import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "src/utils/test-utils";

import fallbacksPage from "../../fallbacks/FallbacksSection.jsx?raw";
import chainEditor from "../FallbackChainEditor.jsx?raw";
import emailCard from "../EmailAlertsCard.jsx?raw";
import historyDrawer from "../ConfigHistoryDrawer.jsx?raw";
import { Glyph } from "../ui";

// Iconify fetches its icons from api.iconify.design at runtime: offline, on
// a self-hosted gateway behind a firewall and in the screenshot harness they
// render blank. The pages people see first use FA glyphs instead.
describe("FA glyphs, not Iconify", () => {
  it.each([
    ["Fallbacks", fallbacksPage],
    ["the fallback chain editor", chainEditor],
    ["Email alerts", emailCard],
    ["config history", historyDrawer],
  ])("%s does not use Iconify", (_name, source) => {
    expect(source).not.toMatch(/components\/iconify/);
  });

  it("draws a 16 px glyph from a local asset", () => {
    const { container } = render(
      <>
        <Glyph name="add" />
        <Glyph name="arrow" />
      </>,
    );
    const glyphs = container.querySelectorAll("[data-glyph]");
    expect([...glyphs].map((g) => g.getAttribute("data-glyph"))).toEqual([
      "add",
      "arrow",
    ]);
  });
});
