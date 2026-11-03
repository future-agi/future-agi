import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import NodeOutputDetail from "../NodeOutputDetail";
import { useGetNodeExecutionDetail } from "src/api/agent-playground/agent-playground";

vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetNodeExecutionDetail: vi.fn(),
}));

vi.mock("ag-grid-react", async () => {
  const React = await import("react");
  const MockAgGrid = React.forwardRef(function MockAgGrid(_props, _ref) {
    return <div data-testid="ag-grid-mock" />;
  });
  return { AgGridReact: MockAgGrid };
});

vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));

vi.mock("src/components/custom-json-viewer/CustomJsonViewer", () => ({
  default: () => null,
}));

vi.mock("src/components/svg-color", () => ({
  default: ({ src, ...props }) => (
    <span data-testid="svg-color" data-src={src} {...props} />
  ),
}));

describe("NodeOutputDetail - Node execution duration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGetNodeExecutionDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    });
  });

  it("renders 'Select a node to view details' when nodeExecutionId is missing", () => {
    render(<NodeOutputDetail executionId="exec-1" nodeExecutionId={null} />);
    expect(screen.getByText("Select a node to view details")).toBeInTheDocument();
  });

  it("displays formatted duration for a successfully completed node", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "success",
        duration_seconds: 5.2,
        inputs: [],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    const durationElement = screen.getByTestId("node-execution-duration");
    expect(durationElement).toBeInTheDocument();
    expect(screen.getByText("5s")).toBeInTheDocument();
  });

  it("displays formatted minutes and seconds for longer executions", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "success",
        duration_seconds: 75,
        inputs: [],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    expect(screen.getByTestId("node-execution-duration")).toBeInTheDocument();
    expect(screen.getByText("1m 15s")).toBeInTheDocument();
  });

  it("displays formatted duration for a completed node that errored/failed", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "failed",
        duration_seconds: 12,
        error_message: "Process timed out",
        inputs: [],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    expect(screen.getByTestId("node-execution-duration")).toBeInTheDocument();
    expect(screen.getByText("12s")).toBeInTheDocument();
  });

  it("guards against currently running nodes and does not show duration", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "running",
        duration_seconds: 10,
        inputs: [{ payload: "test input" }],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    expect(
      screen.queryByTestId("node-execution-duration"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("10s")).not.toBeInTheDocument();
  });

  it("guards against currently pending nodes and does not show duration", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "pending",
        duration_seconds: 0,
        inputs: [],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    expect(
      screen.queryByTestId("node-execution-duration"),
    ).not.toBeInTheDocument();
  });

  it("guards against skipped or unexecuted nodes", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "skipped",
        duration_seconds: null,
        inputs: [],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    expect(
      screen.queryByTestId("node-execution-duration"),
    ).not.toBeInTheDocument();
  });

  it("does not show duration if duration_seconds is null or undefined", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: {
        node_execution_id: "node-exec-1",
        status: "success",
        duration_seconds: null,
        inputs: [],
        outputs: [],
      },
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="exec-1" nodeExecutionId="node-exec-1" />,
    );

    expect(
      screen.queryByTestId("node-execution-duration"),
    ).not.toBeInTheDocument();
  });
});
