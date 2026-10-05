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
      error_message: "Node failed before producing output",
    };
  });

  const renderNodeOutputDetail = () =>
    render(
      <NodeOutputDetail
        executionId="execution-1"
        nodeExecutionId="node-execution-1"
      />,
    );

  it("shows the failure alongside partial node output", () => {
    mockNodeDetail.outputs = [{ payload: "partial result" }];
    mockNodeDetail.error_message = "Provider connection failed";

    renderNodeOutputDetail();

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Provider connection failed",
    );
    expect(screen.getByTestId("node-output-grid")).toHaveTextContent(
      "partial result",
    );
  });

  it("shows the failure when the node has no output", () => {
    renderNodeOutputDetail();

    expect(screen.getByTestId("node-output-grid")).toHaveTextContent(
      "Node failed before producing output",
    );
  });

  it("keeps successful node output unchanged without showing an error", () => {
    mockNodeDetail.status = "success";
    mockNodeDetail.outputs = [{ payload: "complete result" }];
    mockNodeDetail.error_message = "Stale failure from an earlier attempt";

    renderNodeOutputDetail();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("node-output-grid")).toHaveTextContent(
      "complete result",
    );
    expect(screen.getByTestId("node-output-grid")).not.toHaveTextContent(
      "Stale failure from an earlier attempt",
    );
  });
});
