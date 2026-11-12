// W10-TRACING-OTHER-U03: the Agent Graph zoom strip must be reachable
// without a mouse, and every control in it must have a name (matching
// AgentPath's "Zoom in" / "Zoom out" / "Fit to view").
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AgentGraph from "../AgentGraph";

const graphData = {
  nodes: [
    {
      id: "agent:a",
      name: "a",
      type: "agent",
      span_count: 1,
      avg_latency_ms: 10,
      total_tokens: 2,
      total_cost: 0.01,
      error_count: 0,
      trace_count: 1,
    },
  ],
  edges: [],
  path_edges: [],
};

const ZOOM_CONTROL_NAMES = ["Zoom in", "Zoom out", "Fit to view"];

const renderGraph = () =>
  render(<AgentGraph data={graphData} isLoading={false} isError={false} />);

describe("AgentGraph zoom controls (W10-TRACING-OTHER-U03)", () => {
  it("lets keyboard users reach every zoom control without hovering", () => {
    renderGraph();

    [...ZOOM_CONTROL_NAMES, "Fullscreen"].forEach((name) => {
      const control = screen.getByRole("button", { name });
      expect(control).toBeEnabled();
      control.focus();
      expect(control).toHaveFocus();
    });
  });

  it("names the zoom, zoom out and fit controls for mouse users", () => {
    const { container } = renderGraph();
    fireEvent.mouseEnter(container.querySelector(".react-flow").parentElement);

    ZOOM_CONTROL_NAMES.forEach((name) => {
      expect(screen.getByRole("button", { name })).toHaveAttribute(
        "title",
        name,
      );
    });
  });
});
