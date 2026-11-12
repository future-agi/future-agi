import { createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { getCompareChartConfig, getSimpleLineChartConfig } from "../common";

// The alert detail chart legend ("Count Of Errors") used ApexCharts' default
// dark grey label, about 1.8:1 on the dark page. It must take the theme's
// text colour so it reads in both themes.
const THEMES = {
  light: createTheme({ palette: palette("light") }),
  dark: createTheme({ palette: palette("dark") }),
};

const COMPARE_DATA = {
  result: {
    graph_data: [
      { timestamp: "2026-11-09T00:00:00Z", value: 1 },
      { timestamp: "2026-11-10T00:00:00Z", value: 2 },
    ],
    alert_bar_data: [],
  },
};

const STATIC_DATA = {
  result: [
    { timestamp: "2026-11-09T00:00:00Z", value: 1 },
    { timestamp: "2026-11-10T00:00:00Z", value: 2 },
  ],
};

describe("alert chart legend colour", () => {
  for (const [mode, theme] of Object.entries(THEMES)) {
    const isDark = mode === "dark";

    it(`percentage-change chart legend uses the ${mode} text colour`, () => {
      const { options } = getCompareChartConfig(
        COMPARE_DATA,
        { seriesName: "Count Of Errors" },
        { isDark, theme },
      );
      expect(options.legend.labels?.colors).toBe(theme.palette.text.primary);
    });

    it(`static-threshold chart legend uses the ${mode} text colour`, () => {
      const { options } = getSimpleLineChartConfig(
        STATIC_DATA,
        { seriesName: "Count Of Errors" },
        { isDark, theme },
      );
      expect(options.legend.labels?.colors).toBe(theme.palette.text.primary);
    });
  }
});
