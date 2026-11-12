// Generated API-call output never carries a "Simulated" stand-in label: not
// in the column header stats, not in a grid cell. Every gate is open here
// (launch off), so an absent label cannot come from a hidden surface.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("../useDataGridView", () => ({
  useDataGridView: () => ({ density: "comfortable" }),
}));
vi.mock("src/sections/common/Values/gridTyped", () => ({
  default: () => null,
}));

import * as gridView from "../dataGridView";
import CellChrome from "../CellChrome";

const theme = createTheme({ palette: palette("light") });
const HOST = "api.example.com";
const texts = (line) => (line?.parts ?? []).map((p) => p.text);

beforeEach(() => setLaunchOverrideForTest({ launch: false }));
afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("API column header stats", () => {
  it("never lead with a Simulated part for a non-local host", () => {
    const line = gridView.statsLine({
      kind: "pass_fail",
      pass_rate: 0.5,
      fail_count: 2,
      simulated_host: HOST,
    });
    expect(texts(line)).toEqual(["50% pass", "2 fail"]);
  });

  it("show only the progress while the column runs", () => {
    const line = gridView.statsLine(
      { total: 4, completed: 1, simulated_host: HOST },
      { status: "Running" },
    );
    expect(texts(line)).toEqual(["Running · 1/4"]);
    expect(line.progress).toBe(0.25);
  });

  it("offer no stand-in label helper to other grid modules", () => {
    expect(gridView.simulatedLabel).toBeUndefined();
  });
});

describe("API column cells", () => {
  it("render the value without a Simulated tag", () => {
    render(
      <ThemeProvider theme={theme}>
        <CellChrome
          datasetId="ds-1"
          rowId="r-1"
          columnId="c-1"
          columnName="Weather"
          value="sunny"
          cell={{
            dataType: "text",
            originType: "api_call",
            status: "Completed",
            simulatedHost: HOST,
          }}
        >
          <span>sunny</span>
        </CellChrome>
      </ThemeProvider>,
    );
    expect(screen.getByText("sunny")).toBeInTheDocument();
    expect(screen.queryByTestId("api-cell-simulated")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Simulated/);
  });
});
