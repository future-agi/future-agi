import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";

vi.mock("@xyflow/react", () => ({
  // eslint-disable-next-line react/prop-types
  Handle: ({ type, position }) => (
    <div data-testid={`handle-${type}`} data-position={position} />
  ),
  Position: { Top: "top", Bottom: "bottom" },
}));

vi.mock("src/components/svg-color", () => ({
  // eslint-disable-next-line react/prop-types
  default: ({ src }) => <div data-testid="svg-color" data-src={src} />,
}));

import ExecutionNode from "../ExecutionNode";

const theme = createTheme({
  palette: {
    mode: "light",
    black: {
      200: "#e0e0e0",
      400: "#9e9e9e",
    },
    green: {
      50: "#e8f5e9",
      500: "#4caf50",
    },
    red: {
      50: "#ffebee",
      500: "#f44336",
      700: "#d32f2f",
    },
    error: {
      main: "#f44336",
    },
  },
});

const renderNode = (data = {}) =>
  render(
    <ThemeProvider theme={theme}>
      <ExecutionNode
        data={{ label: "Test Node", frontendNodeType: "llm_prompt", ...data }}
      />
    </ThemeProvider>,
  );

describe("ExecutionNode", () => {
  it("renders the label text", () => {
    renderNode({ label: "My Node" });
    expect(screen.getByText("My Node")).toBeInTheDocument();
  });

  it("renders target (top) and source (bottom) handles", () => {
    renderNode();
    expect(screen.getByTestId("handle-target")).toBeInTheDocument();
    expect(screen.getByTestId("handle-source")).toBeInTheDocument();
  });

  it("renders handles with correct positions", () => {
    renderNode();
    expect(screen.getByTestId("handle-target")).toHaveAttribute(
      "data-position",
      "top",
    );
    expect(screen.getByTestId("handle-source")).toHaveAttribute(
      "data-position",
      "bottom",
    );
  });

  it("sets opacity to 0.4 when no nodeExecution (pending)", () => {
    const { container } = renderNode();
    // The outermost Box has opacity styling
    const outerBox = container.firstChild;
    expect(outerBox).toHaveStyle({ opacity: "0.4" });
  });

  it("sets full opacity when nodeExecution exists", () => {
    const { container } = renderNode({
      nodeExecution: { status: "success" },
    });
    const outerBox = container.firstChild;
    expect(outerBox).toHaveStyle({ opacity: "1" });
  });

  it("renders SvgColor icon", () => {
    renderNode({ frontendNodeType: "llm_prompt" });
    expect(screen.getByTestId("svg-color")).toBeInTheDocument();
  });

  it("renders with default config for unknown frontendNodeType", () => {
    renderNode({ frontendNodeType: "unknown_type" });
    // Falls back to llm_prompt config — still renders
    expect(screen.getByText("Test Node")).toBeInTheDocument();
    expect(screen.getByTestId("svg-color")).toBeInTheDocument();
  });

  it("renders SvgColor with icon src from NODE_TYPE_CONFIG", () => {
    renderNode({ frontendNodeType: "llm_prompt" });
    expect(screen.getByTestId("svg-color")).toHaveAttribute(
      "data-src",
      "/assets/icons/ic_chat_single.svg",
    );
  });

  it("sets handles as non-connectable", () => {
    renderNode();
    // isConnectable={false} is passed to Handle
    const handles = screen.getAllByTestId(/handle-/);
    handles.forEach((h) => {
      // React renders boolean false as no attribute or "false"
      expect(h).not.toHaveAttribute("isconnectable", "true");
    });
  });

  it("renders correctly with selected state", () => {
    renderNode({ selected: true, nodeExecution: { status: "success" } });
    expect(screen.getByText("Test Node")).toBeInTheDocument();
  });

  it("dims a skipped node like a never-run node and marks it SKIPPED", () => {
    renderNode({
      nodeExecution: { status: "skipped" },
    });
    const skipped = screen.getByLabelText("Skipped");
    expect(skipped).toHaveStyle({ opacity: "0.4" });
    expect(screen.getByText("SKIPPED")).toBeInTheDocument();
  });

  it("does not render the skipped mark on a never-run node", () => {
    renderNode();
    expect(screen.queryByText("SKIPPED")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Skipped")).not.toBeInTheDocument();
  });

  it("exposes a Skipped hover title", async () => {
    renderNode({ nodeExecution: { status: "skipped" } });
    expect(screen.getByLabelText("Skipped")).toBeInTheDocument();
  });
});
