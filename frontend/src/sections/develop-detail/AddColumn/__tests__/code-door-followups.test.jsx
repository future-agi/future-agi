import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it, vi } from "vitest";
import { palette } from "src/theme/palette";
import OutputCard from "../../DataTab/DatapointDrawerV2/OutputCard";
import { columnActionMenuItems } from "../../DataTab/common";
import { CODE_COLUMNS_DISABLED_REASON } from "../ExecuteCode/codeDisabled";

const theme = createTheme({ palette: palette("light") });
const CONDITIONAL_REASON =
  "Re-running individual conditional cells is not supported. Existing values remain readable.";

const card = (column, cell, onRerun = vi.fn()) =>
  render(
    <ThemeProvider theme={theme}>
      <OutputCard
        output={{
          column: {
            id: "c1",
            name: "stored output",
            data_type: "text",
            ...column,
          },
          cell,
          evals: [],
        }}
        row={{ row_id: "r1" }}
        mode="formatted"
        expectedOptions={[]}
        onToggleEval={() => {}}
        canEdit
        onRerun={onRerun}
      />
    </ThemeProvider>,
  );

const failed = {
  status: "error",
  cell_value: null,
  value_infos: { reason: "stored failure reason" },
};

describe("conditional cell rerun is not a Run Prompt action", () => {
  it.each([
    [
      "code metadata",
      {
        origin_type: "conditional",
        metadata: {
          config: [
            {
              branch_node_config: {
                type: "extract_code",
                config: { code: "def main(): return 1" },
              },
            },
          ],
        },
      },
    ],
    ["metadata omitted by row projection", { origin_type: "conditional" }],
    ["camel-case origin", { originType: "conditional" }],
    [
      "non-code conditional",
      {
        origin_type: "conditional",
        metadata: {
          config: [
            {
              branch_node_config: {
                type: "static_value",
                config: { value: "kept" },
              },
            },
          ],
        },
      },
    ],
  ])(
    "%s: preserves reason without offering the 404 action",
    (_label, column) => {
      const onRerun = vi.fn();
      card(column, failed, onRerun);
      expect(screen.getByText("stored failure reason")).toBeTruthy();
      expect(
        screen.queryByRole("button", { name: /Re-run this cell/ }),
      ).toBeNull();
      expect(screen.getByText(CONDITIONAL_REASON)).toBeTruthy();
      expect(onRerun).not.toHaveBeenCalled();
    },
  );

  it("keeps a successful conditional value readable", () => {
    card(
      { origin_type: "conditional" },
      { status: "pass", cell_value: "saved output" },
    );
    expect(screen.getByText("saved output")).toBeTruthy();
    expect(screen.queryByText(CONDITIONAL_REASON)).toBeNull();
  });

  it("keeps the actual Run Prompt cell action", () => {
    const onRerun = vi.fn();
    card({ origin_type: "run_prompt" }, failed, onRerun);
    fireEvent.click(screen.getByRole("button", { name: /Re-run this cell/ }));
    expect(onRerun).toHaveBeenCalledTimes(1);
  });
});

describe("Python column edit/rerun stays closed regardless of stored config", () => {
  it("does not offer a non-code configuration override", () => {
    const item = columnActionMenuItems({
      id: "c1",
      name: "py",
      originType: "python_code",
      metadata: { config: [] },
    }).find((entry) => entry?.name === "Configure Dynamic Column");
    expect(item.disabled).toBe(true);
    expect(item.tooltip).toBe(CODE_COLUMNS_DISABLED_REASON);
  });

  it("keeps a failed Python cell readable with the original disabled reason", () => {
    card({ origin_type: "python_code" }, failed);
    expect(screen.getByText("stored failure reason")).toBeTruthy();
    expect(screen.getByText(CODE_COLUMNS_DISABLED_REASON)).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /Re-run this cell/ }),
    ).toBeNull();
  });
});
