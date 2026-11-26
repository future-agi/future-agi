import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SessionCellRenderer from "../SessionCellRenderer";
import { getSessionListColumnDef } from "../common";

describe("session cost display", () => {
  it.each([
    [1.2000000000000002, "$1.20"],
    [0, "$0.00"],
    [null, "-"],
    [undefined, "-"],
    [0.000001, "$0.000001"],
    [0.00000004, "< $0.0000001"],
    [0.0015, "$0.0015"],
  ])(
    "formats %s without losing zero, unknown or microdollars",
    (value, expected) => {
      const row = Object.freeze({ total_cost: value });
      render(
        <SessionCellRenderer
          column={{ colId: "total_cost" }}
          value={value}
          data={row}
        />,
      );
      expect(screen.getByText(expected)).toBeInTheDocument();
      expect(row.total_cost).toBe(value);
    },
  );

  it("keeps the underlying numeric field for sorting and filtering", () => {
    const column = getSessionListColumnDef({
      id: "total_cost",
      name: "Cost",
      isVisible: true,
    });
    expect(column.field).toBe("total_cost");
    expect(column.sortable).toBe(true);
    expect(column.valueGetter).toBeUndefined();
    expect(column.valueFormatter).toBeUndefined();
    expect(column.cellRenderer).toBe(SessionCellRenderer);
  });
});
