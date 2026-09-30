import { createTheme, getContrastRatio } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { chartTooltipProps } from "../DashboardCharts";

const themeFor = (mode) => createTheme({ palette: palette(mode) });

describe.each(["light", "dark"])("chartTooltipProps in the %s theme", (mode) => {
  const theme = themeFor(mode);
  const props = chartTooltipProps(theme);
  const background = props.contentStyle.backgroundColor;

  it("puts the box on the theme's paper colour, not a fixed dark one", () => {
    expect(background).toBe(theme.palette.background.paper);
  });

  it("gives the rows and the heading a readable colour of their own", () => {
    // Recharts colours each row itself (black when the series has none), so
    // the box's own text colour never reaches them.
    for (const style of [props.itemStyle, props.labelStyle]) {
      expect(getContrastRatio(style.color, background)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("sits above the donut's centre label", () => {
    expect(props.wrapperStyle.zIndex).toBeGreaterThan(0);
  });
});
