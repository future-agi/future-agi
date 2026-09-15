import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
    <span data-testid="duration-icon" data-src={src} {...props} />
  ),
}));

const renderNodeDetail = (detail) => {
  useGetNodeExecutionDetail.mockReturnValue({
    data: detail,
    isLoading: false,
    isError: false,
  });

  return render(
    <NodeOutputDetail executionId="execution-1" nodeExecutionId="node-1" />,
  );
};

describe("NodeOutputDetail duration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("formats a completed node duration in seconds", () => {
    renderNodeDetail({
      status: "success",
      duration_seconds: 5.2,
      inputs: [],
      outputs: [],
    });

    expect(screen.getByTestId("node-execution-duration")).toHaveAttribute(
      "aria-label",
      "Duration: 5.2s",
    );
    expect(screen.getByText("5.2s")).toBeInTheDocument();
  });

  it("formats longer completed node durations as minutes and seconds", () => {
    renderNodeDetail({
      status: "success",
      duration_seconds: 75,
      inputs: [],
      outputs: [],
    });

    expect(screen.getByText("1m 15s")).toBeInTheDocument();
  });

  it("shows duration for a completed failed node", () => {
    renderNodeDetail({
      status: "failed",
      duration_seconds: 12,
      error_message: "Process timed out",
      inputs: [],
      outputs: [],
    });

    expect(screen.getByText("12s")).toBeInTheDocument();
  });

  it.each(["running", "pending", "skipped", "idle"])(
    "does not show duration for a %s node",
    (status) => {
      renderNodeDetail({
        status,
        duration_seconds: 10,
        inputs: [],
        outputs: [],
      });

      expect(
        screen.queryByTestId("node-execution-duration"),
      ).not.toBeInTheDocument();
    },
  );

  it("does not show duration when the API has no elapsed time", () => {
    renderNodeDetail({
      status: "success",
      duration_seconds: null,
      inputs: [],
      outputs: [],
    });

    expect(
      screen.queryByTestId("node-execution-duration"),
    ).not.toBeInTheDocument();
  });

  it("does not show duration for a node that never ran", () => {
    useGetNodeExecutionDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: false,
    });

    render(
      <NodeOutputDetail executionId="execution-1" nodeExecutionId={null} />,
    );

    expect(
      screen.getByText("Select a node to view details"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("node-execution-duration"),
    ).not.toBeInTheDocument();
  });
});
