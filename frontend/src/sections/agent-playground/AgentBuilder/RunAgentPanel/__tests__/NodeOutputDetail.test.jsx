/* eslint-disable react/prop-types */
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import NodeOutputDetail from "../NodeOutputDetail";

let mockNodeDetail;

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeExecutionDetail: () => ({
    data: mockNodeDetail,
    isLoading: false,
    isError: false,
  }),
}));

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  return {
    AgGridReact: ReactModule.forwardRef(function MockAgGridReact(
      { rowData },
      _ref,
    ) {
      return (
        <pre data-testid="node-output-grid">{JSON.stringify(rowData)}</pre>
      );
    }),
  };
});

describe("NodeOutputDetail", () => {
  beforeEach(() => {
    mockNodeDetail = {
      status: "failed",
      node_execution_id: "node-execution-1",
      inputs: [],
      outputs: [],
      error_message: null,
    };
  });

  it("shows a failure alongside partial node output", () => {
    mockNodeDetail.outputs = [{ payload: "partial result" }];
    mockNodeDetail.error_message = "Provider connection failed";

    render(
      <NodeOutputDetail
        executionId="execution-1"
        nodeExecutionId="node-execution-1"
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Provider connection failed",
    );
    expect(screen.getByTestId("node-output-grid")).toHaveTextContent(
      "partial result",
    );
  });

  it("keeps the existing no-output error presentation", () => {
    mockNodeDetail.error_message = "Node failed before producing output";

    render(
      <NodeOutputDetail
        executionId="execution-1"
        nodeExecutionId="node-execution-1"
      />,
    );

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("node-output-grid")).toHaveTextContent(
      "Node failed before producing output",
    );
  });

  it("does not show an error for a successful node", () => {
    mockNodeDetail.status = "success";
    mockNodeDetail.outputs = [{ payload: "complete result" }];
    mockNodeDetail.error_message = "Stale failure from an earlier attempt";

    render(
      <NodeOutputDetail
        executionId="execution-1"
        nodeExecutionId="node-execution-1"
      />,
    );

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("node-output-grid")).toHaveTextContent(
      "complete result",
    );
  });
});
