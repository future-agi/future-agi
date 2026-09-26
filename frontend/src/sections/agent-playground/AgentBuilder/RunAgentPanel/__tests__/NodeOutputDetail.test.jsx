/* eslint-disable react/prop-types */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import NodeOutputDetail from "../NodeOutputDetail";

let detail;

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeExecutionDetail: () => ({
    data: detail,
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: React.forwardRef(function MockAgGridReact(
    { columnDefs, rowData },
    _ref,
  ) {
    return (
      <div>
        {columnDefs.map((column) => (
          <span key={column.field}>{column.headerName}</span>
        ))}
        {rowData.map((row, rowIndex) =>
          columnDefs.map((column) => {
            if (column.cellRenderer) {
              return React.createElement(column.cellRenderer, {
                key: `${rowIndex}-${column.field}`,
                data: row,
                value: row[column.field],
              });
            }
            const value = row[column.field];
            const text = Array.isArray(value)
              ? value.map((item) => item.payload).join(" ")
              : value;
            return (
              <span key={`${rowIndex}-${column.field}`}>
                {typeof text === "string" ? text : ""}
              </span>
            );
          }),
        )}
      </div>
    );
  }),
}));

describe("NodeOutputDetail errors", () => {
  it("renders partial output and backend error for failed nodes", () => {
    detail = {
      status: "failed",
      outputs: [{ payload: "partial" }],
      errorMessage: "backend failed",
    };
    render(<NodeOutputDetail executionId="e" nodeExecutionId="n" />);
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("backend failed")).toBeInTheDocument();
  });

  it("keeps no-output failures in the output error cell", () => {
    detail = { status: "error", outputs: [], errorMessage: "backend failed" };
    render(<NodeOutputDetail executionId="e" nodeExecutionId="n" />);
    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getByText("backend failed")).toBeInTheDocument();
  });

  it("does not render stale errors for successful nodes", () => {
    detail = {
      status: "success",
      outputs: [{ payload: "ok" }],
      errorMessage: "stale",
    };
    render(<NodeOutputDetail executionId="e" nodeExecutionId="n" />);
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.queryByText("Error")).not.toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });

  it("does not render a stale error for a successful node with no output", () => {
    detail = { status: "success", outputs: [], errorMessage: "stale" };
    render(<NodeOutputDetail executionId="e" nodeExecutionId="n" />);
    expect(screen.queryByText("Error")).not.toBeInTheDocument();
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });
  it("repeats the node-level error on every paired row", () => {
    detail = {
      status: "failed",
      inputs: [{ payload: "in-a" }, { payload: "in-b" }],
      outputs: [{ payload: "out-a" }, { payload: "out-b" }],
      errorMessage: "backend failed",
    };
    render(<NodeOutputDetail executionId="e" nodeExecutionId="n" />);
    expect(screen.getByText("out-a")).toBeInTheDocument();
    expect(screen.getByText("out-b")).toBeInTheDocument();
    // The error is node-level, not per-port, so the same string appears on
    // both rows — the same way the no-output error path already fills one
    // row per input.
    expect(screen.getAllByText("backend failed")).toHaveLength(2);
  });

  it("keeps the input port list for a failed node with unequal port counts", () => {
    detail = {
      status: "failed",
      inputs: [{ payload: "in-a" }, { payload: "in-b" }],
      outputs: [{ payload: "partial" }],
      errorMessage: "backend failed",
    };
    render(<NodeOutputDetail executionId="e" nodeExecutionId="n" />);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByText("in-a")).toBeInTheDocument();
    expect(screen.getByText("in-b")).toBeInTheDocument();
    expect(screen.getByText("partial")).toBeInTheDocument();
    expect(screen.getByText("backend failed")).toBeInTheDocument();
  });
});
