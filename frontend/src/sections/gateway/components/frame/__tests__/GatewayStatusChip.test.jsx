import React from "react";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { GatewayStatusChip } from "..";

function chipColor(tone, mode = "light") {
  const theme = createTheme({ palette: palette(mode), typography });
  render(
    <ThemeProvider theme={theme}>
      <GatewayStatusChip tone={tone} label={`chip-${tone}-${mode}`} />
    </ThemeProvider>,
  );
  const chip = screen
    .getByText(`chip-${tone}-${mode}`)
    .closest(".MuiChip-root");
  return { color: window.getComputedStyle(chip).color, theme };
}

function rgb(hex) {
  const n = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16));
  return `rgb(${r}, ${g}, ${b})`;
}

// The neutral chip ("Not enforced on this gateway",
// "Warns", "Needs a gateway plugin") was #9c9c9c on #f6f6f8, 2.54:1. The
// Future AGI status chip's opt-in neutralTextColor (TH-4088, used on Observe)
// draws it in text.secondary instead.
describe("GatewayStatusChip contrast", () => {
  it("neutral tone passes neutralTextColor: text.secondary, light and dark", () => {
    for (const mode of ["light", "dark"]) {
      const { color, theme } = chipColor("neutral", mode);
      expect(color).toBe(rgb(theme.palette.text.secondary));
    }
  });

  it("leaves the other tones on the shared status colours", () => {
    const { color, theme } = chipColor("healthy");
    expect(color).not.toBe(rgb(theme.palette.text.secondary));
  });
});
