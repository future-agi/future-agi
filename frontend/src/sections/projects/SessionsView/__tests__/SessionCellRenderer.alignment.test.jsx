import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SessionCellRenderer from "../SessionCellRenderer";

// L06: session grid headers sit at the start of each 200px column. A value
// pinned to the column end fell past the grid's right edge at 1024px while its
// header (Duration) was still on screen, leaving only the clock icon visible.
const renderCell = (colId, value) =>
  render(
    <SessionCellRenderer
      column={{ colId }}
      value={value}
      data={Object.freeze({ [colId]: value })}
    />,
  );

describe("session grid value alignment", () => {
  it("starts the Duration value under its header", () => {
    renderCell("duration", 69);
    expect(screen.getByText("69s")).toHaveStyle({
      justifyContent: "flex-start",
    });
  });

  it.each([
    ["total_cost", 0.000084, "$0.000084"],
    ["total_tokens", 1200, "1200"],
    ["total_traces_count", 3, "3"],
  ])("starts the %s value under its header", (colId, value, text) => {
    renderCell(colId, value);
    expect(screen.getByText(text)).toHaveStyle({
      justifyContent: "flex-start",
    });
  });

  it("starts the Start Time value under its header", () => {
    renderCell("start_time", "2026-11-10T10:00:00.123456");
    expect(screen.getByText(/^2026\/11\/10, /)).toHaveStyle({
      justifyContent: "flex-start",
    });
  });

  it("starts the cell wrapper at the column start", () => {
    const { container } = renderCell("duration", 69);
    expect(container.querySelector(".session-cell-text")).toHaveStyle({
      justifyContent: "flex-start",
    });
  });
});
