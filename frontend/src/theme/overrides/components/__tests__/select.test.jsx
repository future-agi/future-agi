import { FormControl, InputLabel } from "@mui/material";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { palette } from "src/theme/palette";
import { select } from "../select";

const base = createTheme({ palette: palette("light") });
const theme = createTheme(base, { components: select(base) });

// The rule also paints the label; jsdom can't rank its padding by specificity.
const painted = (variant) => {
  render(
    <ThemeProvider theme={theme}>
      <FormControl variant={variant} focused>
        <InputLabel shrink>{variant}</InputLabel>
      </FormControl>
    </ThemeProvider>,
  );
  return (
    getComputedStyle(screen.getByText(variant)).backgroundColor ===
    "rgb(255, 255, 255)"
  );
};

describe("select theme override", () => {
  it("pads a focused label only when it sits inside the field, so an outlined one doesn't shift", () => {
    expect(painted("filled")).toBe(true);
    expect(painted("outlined")).toBe(false);
  });
});
