import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

  const folded = () =>
    render(
      <ThemeProvider theme={theme}>
        <CitedText
          text="The agent keeps calling a declined refund complete. #1 #2 #3 #4 #5 #6"
          hiddenRefs={new Set([5, 6])}
          moreRefs={[5, 6]}
          component="p"
        />
      </ThemeProvider>,
    ).container.querySelector("p");

  it("sets folded refs out in the line on a click, not over the text", () => {
    const line = folded();
    fireEvent.click(
      within(line).getByRole("button", { name: /2 more examples/ }),
      { detail: 1 },
    );
    // In the paragraph's own flow, where "+2" was, free to wrap; no
    // floating card holds them.
    const chips = ["Open example 5", "Open example 6"].map((name) =>
      within(line).getByRole("button", { name }),
    );
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(within(line).queryByText("+2")).toBeNull();
    expect(getComputedStyle(chips[0].parentElement).whiteSpace).toBe("normal");
    // A mouse click leaves focus where the pointer is.
    expect(chips[0]).not.toHaveFocus();
  });

  it("moves focus to the first ref set out when opened from the keyboard", () => {
    const line = folded();
    const more = within(line).getByRole("button", {
      name: "Show 2 more examples",
    });
    more.focus();
    // Enter or Space on a button clicks it with no pointer detail.
    fireEvent.click(more, { detail: 0 });
    expect(
      within(line).getByRole("button", { name: "Open example 5" }),
    ).toHaveFocus();
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
