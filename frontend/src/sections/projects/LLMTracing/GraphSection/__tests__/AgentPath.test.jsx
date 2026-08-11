import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import AgentPath from "../AgentPath";

describe("AgentPath failure state", () => {
  it("shows loading before validating absent pending data", () => {
    render(<AgentPath data={undefined} isLoading isError={false} />);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.getByText("Loading graph data…")).toBeInTheDocument();
  });

  it("shows a sanitized retry message instead of a false empty state", () => {
    render(<AgentPath data={undefined} isLoading={false} isError />);

    expect(
      screen.getByText(
        "We couldn't load the agent path. Please retry in a moment.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No agent path data available for this time range"),
    ).not.toBeInTheDocument();
  });

  it("shows the exact empty state after a completed query", () => {
    render(
      <AgentPath
        data={{ nodes: [], edges: [], path_edges: [] }}
        isLoading={false}
        isError={false}
      />,
    );

    expect(
      screen.getByText("No agent path data available for this time range"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
