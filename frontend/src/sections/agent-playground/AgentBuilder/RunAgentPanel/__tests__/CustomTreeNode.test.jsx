import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { CustomTreeNode } from "../CustomTreeNode";

const renderNode = (node) =>
  render(
    <CustomTreeNode
      node={node}
      depth={0}
      isSelected={false}
      isExpanded={false}
      hasChildren={false}
      onSelect={vi.fn()}
      onToggle={vi.fn()}
    />,
  );

describe("CustomTreeNode", () => {
  it("renders duration while omitting unavailable token and cost values", () => {
    renderNode({
      id: "node-1",
      name: "Generate answer",
      type: "llm_prompt",
      duration: 1250,
    });

    expect(screen.getByText("Generate answer")).toBeInTheDocument();
    expect(screen.getByText("1.25s")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(screen.queryByText("0.0000")).not.toBeInTheDocument();
  });

  it("renders zero metrics when they were explicitly supplied", () => {
    renderNode({
      id: "node-1",
      name: "Generate answer",
      type: "llm_prompt",
      duration: 0,
      tokens: 0,
      cost: 0,
    });

    expect(screen.getByText("0ms")).toBeInTheDocument();
    expect(screen.getByText("0.0000")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
