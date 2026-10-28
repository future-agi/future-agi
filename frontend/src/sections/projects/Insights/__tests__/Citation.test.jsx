import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { CitedText } from "../Citation";

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
