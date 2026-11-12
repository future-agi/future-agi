import { render, screen } from "src/utils/test-utils";
import { createTheme, getContrastRatio } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import CardWrapper from "../components/CardWrapper";

// The app's theme, built as theme/index.jsx builds it.
const appTheme = (mode) => {
  const theme = createTheme({
    palette: palette(mode),
    typography,
    customShadows: customShadows(mode),
    shape: dsShape,
  });
  theme.components = componentsOverrides(theme);
  return theme;
};

const rgb = (hex) =>
  `rgb(${hex
    .slice(1)
    .match(/../g)
    .map((v) => parseInt(v, 16))
    .join(", ")})`;

// The four numbered sections of the create-alert form.
const STEPS = [
  "Define Metrics & Interval",
  "Filter Events",
  "Define Alert",
  "Define Notification",
];

describe("Create alert step numbers", () => {
  it.each(["light", "dark"])("read at 4.5:1 or better in %s", (mode) => {
    const theme = appTheme(mode);
    const { secondary } = theme.palette.text;
    // The number sits on its own background.neutral tile.
    expect(
      getContrastRatio(secondary, theme.palette.background.neutral),
    ).toBeGreaterThanOrEqual(4.5);

    render(
      <>
        {STEPS.map((title, order) => (
          <CardWrapper key={title} order={order} title={title} />
        ))}
      </>,
      { theme },
    );

    // text.disabled took the numbers to about 2.4:1.
    STEPS.forEach((title, order) => {
      expect(screen.getByText(title)).toBeInTheDocument();
      const number = screen.getByText(String(order + 1));
      expect(getComputedStyle(number).color).toBe(rgb(secondary));
    });
  });
});
