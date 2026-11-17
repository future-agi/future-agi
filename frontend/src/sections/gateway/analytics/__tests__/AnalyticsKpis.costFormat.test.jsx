// The Cost KPI uses the money format of the grids on the same page
// (grouped thousands, the same digits), so a total reads the way its rows do.
import React from "react";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import AnalyticsKpis from "../AnalyticsKpis";

const renderKpis = (overview) => {
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <AnalyticsKpis overview={overview} period="24 hours" />
    </ThemeProvider>,
  );
  return within(screen.getByRole("list", { name: "Key metrics" }));
};

describe("Analytics KPI: the Cost figure", () => {
  it("groups thousands like the grids on the page", () => {
    const kpis = renderKpis({
      total_requests: { value: 14, trend: null },
      total_cost: { value: "26265.65046", trend: null },
    });
    expect(kpis.getByText("Cost").nextSibling).toHaveTextContent(
      /^\$26,265\.65046/,
    );
  });
});
