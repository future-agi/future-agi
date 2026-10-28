import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { CitedText, EvidenceQuote } from "../Citation";

vi.mock("src/components/iconify", () => ({ default: () => null }));

const theme = createTheme({ palette: palette("light"), typography });

describe("CitedText", () => {
  it.each([
    "Refund loops. See #1, #3, #5, #6, #7, #8, #9, #10.",
    "Refund loops. See (#1, #3, #5, #6, #7), #8, #9, #10.",
    "Refund loops. See (#1, #3, #5, #6, #7) and #8; #9 & #10.",
  ])("leaves no stray separators where folded refs were: %s", (prose) => {
    const hidden = [7, 8, 9, 10];
    const { container } = render(
      <ThemeProvider theme={theme}>
        <CitedText
          text={prose}
          hiddenRefs={new Set(hidden)}
          moreRefs={hidden}
        />
      </ThemeProvider>,
    );
    const text = container.textContent.replace(/\s+/g, " ");
    expect(text).toContain("+4");
    expect(text).not.toMatch(/,\s*,/);
    expect(text.trim()).toMatch(/\+4\.$/);
  });
});

describe("EvidenceQuote", () => {
  const shown = (quote) =>
    render(
      <ThemeProvider theme={theme}>
        <EvidenceQuote quote={quote} />
      </ThemeProvider>,
    ).container.querySelector("blockquote");

  it("wraps words the agent said in quote marks", () => {
    const block = shown("Yes, the export is running.");
    expect(block.textContent).toBe("“Yes, the export is running.”");
    expect(getComputedStyle(block).fontStyle).toBe("italic");
  });

  it.each([
    ['"status": "failed"'],
    ['{"sent": true, "expires_minutes": 30}'],
    ["[1, 2]"],
  ])("shows a JSON fragment as written, as code: %s", (quote) => {
    const block = shown(quote);
    expect(block.textContent).toBe(quote);
    // Set as code: upright and as laid out (jsdom can't read the font
    // stack itself).
    expect(getComputedStyle(block).fontStyle).toBe("normal");
    expect(getComputedStyle(block).whiteSpace).toBe("pre-wrap");
  });

  it("doesn't add quote marks to a quote that carries its own", () => {
    expect(shown("“Cancel it,” I said.”").textContent).toBe(
      "“Cancel it,” I said.”",
    );
    expect(shown('"Cancel it now"').textContent).toBe('"Cancel it now"');
  });
});
