/* eslint-disable react/prop-types */
// A reference column whose evaluation was edited in place keeps its old
// scores; the server marks them. The header shows "Scored before edit" with
// its tooltip only for a marked column, and a marked cell keeps its value,
// muted, with its own tooltip.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { getEachColumnDef } from "../tableConfig";
import CustomExperimentColumnHeader from "../CustomExperimentColumnHeader";

vi.mock("src/sections/common/DevelopCellRenderer/CustomCellRender", () => ({
  default: ({ value }) => <span data-testid="cell-value">{value}</span>,
}));
vi.mock("../states", () => ({
  useRerunColumnInExperimentStoreShallow: () => () => {},
}));

const CHIP = "Scored before edit";
const CHIP_TIP = "These scores are from this evaluation before it was edited.";
const CELL_TIP = "Scored before the evaluation was edited";

const theme = createTheme({
  palette: palette("light"),
  customShadows: { drawer: "none" },
});
const wrap = (node) =>
  render(<ThemeProvider theme={theme}>{node}</ThemeProvider>);

const column = (extra = {}) => ({
  id: "col-tone",
  name: "Tone",
  origin_type: "evaluation",
  data_type: "text",
  ...extra,
});

function renderCell(cell) {
  const def = getEachColumnDef(column({ scored_before_edit: true }));
  const Renderer = def.cellRenderer;
  wrap(
    <Renderer
      value={cell.cell_value}
      data={{ row_id: "r1", [def.field]: cell }}
      colDef={{ field: def.field }}
    />,
  );
}

describe("Scored before edit", () => {
  it("shows the chip and its one-sentence tooltip on a marked reference column", async () => {
    wrap(
      <CustomExperimentColumnHeader
        displayName="Tone"
        col={column({ scored_before_edit: true })}
        hideMenu
      />,
    );
    const chip = screen.getByText(CHIP);
    fireEvent.mouseOver(chip);
    // Exactly this sentence: no action exists to point to.
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent.trim()).toBe(CHIP_TIP);
  });

  it("shows no chip when the column is not marked", () => {
    wrap(
      <CustomExperimentColumnHeader
        displayName="Tone"
        col={column({ scored_before_edit: false })}
        hideMenu
      />,
    );
    expect(screen.getByText("Tone")).toBeInTheDocument();
    expect(screen.queryByText(CHIP)).not.toBeInTheDocument();
  });

  it("keeps a marked cell's value, muted, with its tooltip", async () => {
    renderCell({
      cell_value: "Passed",
      value_infos: { reason: "fine", scored_before_edit: true },
    });
    expect(screen.getByTestId("cell-value")).toHaveTextContent("Passed");
    const muted = screen.getByTestId("scored-before-edit-cell");
    expect(muted).toHaveStyle({ opacity: "0.55" });
    fireEvent.mouseOver(muted);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(CELL_TIP);
  });

  it("shows a cell scored after the edit as it always did", () => {
    renderCell({ cell_value: "Failed", value_infos: { reason: "new" } });
    expect(screen.getByTestId("cell-value")).toHaveTextContent("Failed");
    expect(
      screen.queryByTestId("scored-before-edit-cell"),
    ).not.toBeInTheDocument();
  });
});
