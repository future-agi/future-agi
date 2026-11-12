import React from "react";
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { createTheme } from "@mui/material/styles";
import { render, screen, within } from "src/utils/test-utils";
import DataTable from "src/components/data-table/DataTable";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { DEFAULT_COLUMN_CONFIG, useColumns } from "../evalUsageColumns";

// W10-EVALS-U06: usage logs are always returned newest first and the Usage
// tab cannot re-sort them, so no log header may offer a sort control.

const theme = createTheme({
  palette: palette("light"),
  typography,
  spacing: (factor) => `${0.25 * factor}rem`,
});

// The default columns plus one discovered input variable (generic column).
const CONFIG = [
  ...DEFAULT_COLUMN_CONFIG,
  {
    value: "input_var_topic",
    label: "topic",
    enabled: true,
    is_visible: true,
    order_index: DEFAULT_COLUMN_CONFIG.length,
  },
];

const HEADERS = [
  "Score",
  "Result",
  "Input",
  "Reason",
  "Source",
  "Recorded version",
  "Ran at",
  "topic",
];

const ROW = {
  id: "usage-1",
  score: 0.9,
  result: "Passed",
  input: "What is the refund window?",
  reason: "Cites the policy",
  source: "eval_playground",
  version: 3,
  created_at: "2026-11-02T10:00:00Z",
  input_var_topic: "billing",
};

describe("usage log columns sort affordance (W10-EVALS-U06)", () => {
  it("W10-EVALS-U06 shows no sort control on the usage log headers", async () => {
    const { result } = renderHook(() => useColumns(CONFIG));
    render(
      <div style={{ height: 400, width: 1400 }}>
        <DataTable columns={result.current} data={[ROW]} rowCount={1} />
      </div>,
      { theme },
    );

    expect(
      await screen.findByRole("columnheader", { name: "Ran at" }),
    ).toBeInTheDocument();
    HEADERS.forEach((label) => {
      const header = screen.getByRole("columnheader", { name: label });
      expect(
        within(header).queryByRole("button", { name: "Sort", hidden: true }),
      ).toBeNull();
    });
    expect(
      screen.queryAllByRole("button", { name: "Sort", hidden: true }),
    ).toHaveLength(0);
  });
});
