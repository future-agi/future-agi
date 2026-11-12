// The Row view header's source pill shows only in a build that ships the row
// source surface, and names the source by the label the dataset read sends:
// a span import reads as a span, never as a manual row.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const { Null } = vi.hoisted(() => ({ Null: () => null }));
vi.mock("src/components/iconify", () => ({ default: Null }));
vi.mock("src/sections/common/Datasets/datasetKind", () => ({
  useDatasetKind: () => ({ kind: null, dataset: null }),
}));
vi.mock("src/api/develop/dataset-versions", () => ({
  useDatasetVersions: () => ({ data: undefined }),
}));
vi.mock("src/sections/common/Collab/RowCollabPills", () => ({
  default: Null,
}));
vi.mock("../../Splits/AssignSplitsMenu", () => ({ default: Null }));
vi.mock("src/sections/common/Datasets/SplitChips", () => ({ default: Null }));

import { RowHeaderLine2 } from "../RowViewHeader";

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
const SPAN = { ...TRACE, type: "span", label: "Span 1a2b3c4d" };
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
const line2 = (source, handlers = {}) =>
  render(
    <ThemeProvider theme={theme}>
      <RowHeaderLine2
        datasetId="ds-1"
        row={{ row_id: "R1", order: 0, row_meta: { source } }}
        splits={[]}
        canEdit={false}
        splitAnchor={null}
        setSplitAnchor={() => {}}
        splitChipRef={{ current: null }}
        {...handlers}
      />
    </ThemeProvider>,
  );

afterEach(() => {
  cleanup();
  setLaunchOverrideForTest(null);
});

describe("Row header source pill", () => {
  it.each([
    ["trace", TRACE],
    ["span", SPAN],
    ["session", SESSION],
  ])("is absent for a %s source without the claim", (_, source) => {
    launch(false);
    line2(source);
    expect(screen.queryByTestId("row-source-pill")).toBeNull();
  });

  it("names a trace import and opens its trace", () => {
    launch(true);
    const onOpenTrace = vi.fn();
    line2(TRACE, { onOpenTrace });
    const pill = screen.getByTestId("row-source-pill");
    expect(pill.textContent).toContain("From Trace 7e1a2b3c");
    fireEvent.click(pill);
    expect(onOpenTrace).toHaveBeenCalledTimes(1);
  });

  it("names a span import as a span and opens its trace", () => {
    launch(true);
    const onOpenTrace = vi.fn();
    line2(SPAN, { onOpenTrace });
    const pill = screen.getByTestId("row-source-pill");
    expect(pill.textContent).toContain("From Span 1a2b3c4d");
    expect(pill.textContent).not.toContain("Added manually");
    fireEvent.click(pill);
    expect(onOpenTrace).toHaveBeenCalledTimes(1);
  });

  it("names a session import and opens its conversation", () => {
    launch(true);
    const onOpenConversation = vi.fn();
    line2(SESSION, { onOpenConversation });
    const pill = screen.getByTestId("row-source-pill");
    expect(pill.textContent).toContain("From Session 5d6e7f80");
    fireEvent.click(pill);
    expect(onOpenConversation).toHaveBeenCalledTimes(1);
  });
});
