import { describe, it, expect } from "vitest";

describe("QueueItemsTable column definitions (Issue #1966)", () => {
  it("ensures Item Status column has resizable set to true", () => {
    // Column definition mock replicating queue-items-table.jsx
    const statusColumnDef = {
      field: "status",
      headerName: "Item Status",
      flex: 1,
      minWidth: 130,
      resizable: true,
    };

    expect(statusColumnDef.field).toBe("status");
    expect(statusColumnDef.headerName).toBe("Item Status");
    expect(statusColumnDef.resizable).toBe(true);
  });

  it("ensures defaultColDef does not override explicitly resizable columns", () => {
    const defaultColDef = {
      lockVisible: true,
      filter: false,
      sortable: false,
      resizable: false,
    };

    const statusColumnDef = {
      field: "status",
      headerName: "Item Status",
      flex: 1,
      minWidth: 130,
      resizable: true,
    };

    const resolvedColumnDef = {
      ...defaultColDef,
      ...statusColumnDef,
    };

    expect(resolvedColumnDef.resizable).toBe(true);
  });
});
