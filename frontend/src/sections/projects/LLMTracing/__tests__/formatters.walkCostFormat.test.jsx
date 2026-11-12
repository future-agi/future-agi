import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { formatCost } from "../formatters";
import SessionCellRenderer from "../../SessionsView/SessionCellRenderer";

// OBS-SW-127: the walk saw summed costs print as raw floats in the shared trace
// view, the session drawer, the session grid and the user page. Every one of
// them prints its cost through this formatCost.
describe("OBS-SW-127 summed costs print as money, not float noise", () => {
  it.each([
    [0.000042000000000000004, "$0.000042"],
    [0.00012600000000000003, "$0.000126"],
    [0.000029999999999999997, "$0.00003"],
    [1.2000000000000002, "$1.20"],
  ])("formats the float %s as %s", (value, expected) => {
    expect(formatCost(value)).toBe(expected);
    expect(formatCost(value)).not.toMatch(/\d{10,}/);
  });

  it("keeps every digit of a decimal string", () => {
    expect(formatCost("0.0001234567890123456789")).toBe(
      "$0.0001234567890123456789",
    );
  });

  it("shows a session's summed cost as money in the session grid and on the user page", () => {
    const row = Object.freeze({ total_cost: 0.00012600000000000003 });
    render(
      <SessionCellRenderer
        column={{ colId: "total_cost" }}
        value={row.total_cost}
        data={row}
      />,
    );
    expect(screen.getByText("$0.000126")).toBeInTheDocument();
    expect(
      screen.queryByText(/0\.00012600000000000003/),
    ).not.toBeInTheDocument();
    expect(row.total_cost).toBe(0.00012600000000000003);
  });
});
