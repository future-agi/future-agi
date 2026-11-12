// The grid's Source cell shows a row's import source only in a build that
// ships the row source surface. Without it the cell reads as a row with no
// source: no chip to open a trace or a conversation.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { Null } = vi.hoisted(() => ({ Null: () => null }));
vi.mock("src/components/iconify", () => ({ default: Null }));

import { SourceCell, useSourceTraceStore } from "../SystemCells";

const SURFACE = "dataset.rowExtras.conversation";
const theme = createTheme({ palette: palette("light") });
const TRACE = {
  type: "trace",
  label: "Trace 7e1a2b3c",
  project_id: "00000000-0000-4000-8000-0000000000b1",
  trace_id: "7e1a2b3c-0000-4000-8000-000000000001",
  span_id: "1a2b3c4d5e6f7081",
  expired: false,
};
const SESSION = {
  type: "session",
  label: "Session 5d6e7f80",
  project_id: TRACE.project_id,
  session_id: "5d6e7f80-0000-4000-8000-000000000002",
  expired: false,
};

const launch = (claimed) =>
  setLaunchOverrideForTest({
    launch: true,
    experimentsBasic: false,
    madeReal: claimed ? [SURFACE] : [],
  });
const cell = (source) =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={["/dashboard/develop/ds-1?tab=data"]}>
        <SourceCell
          data={{ row_id: "R1", order: 0, row_meta: { source } }}
          context={{ datasetId: "ds-1" }}
        />
      </MemoryRouter>
    </ThemeProvider>,
  );

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
  useSourceTraceStore.getState().close();
});

describe("grid Source cell", () => {
  it.each([
    ["trace", TRACE],
    ["session", SESSION],
    ["expired trace", { ...TRACE, expired: true }],
  ])("shows no source for a %s source without the claim", (_, source) => {
    launch(false);
    cell(source);
    const chip = screen.getByTestId("source-chip");
    expect(chip.tagName).toBe("SPAN");
    expect(chip.textContent).toBe("—");
  });

  it("opens the live trace with the claim", () => {
    launch(true);
    cell(TRACE);
    const chip = screen.getByTestId("source-chip");
    expect(chip.tagName).toBe("BUTTON");
    fireEvent.click(chip);
    expect(useSourceTraceStore.getState().trace).toEqual({
      traceId: TRACE.trace_id,
      projectId: TRACE.project_id,
      spanId: TRACE.span_id,
    });
  });

  it("opens no live trace for an expired source with the claim", () => {
    launch(true);
    cell({ ...TRACE, expired: true });
    fireEvent.click(screen.getByTestId("source-chip"));
    expect(useSourceTraceStore.getState().trace).toBeNull();
  });
});
