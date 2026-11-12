import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import AgentNodeSetupDialog from "../AgentNodeSetupDialog";

// The module that exports AGENT_NODE_AVAILABILITY also holds the hook, which
// imports these two. The dialog never calls them.
vi.mock("src/api/agent-playground/agent-playground", () => ({
  useGetReferenceableGraphs: vi.fn(),
}));

vi.mock("../../store", () => ({
  useAgentPlaygroundStoreShallow: vi.fn(),
}));

const renderEmpty = () =>
  render(
    <AgentNodeSetupDialog
      open
      status="empty"
      onRefresh={vi.fn()}
      onAdd={vi.fn()}
      onClose={vi.fn()}
    />,
  );

// A new workspace has one agent, the one being built, so picking Agent Node
// lands here first.
describe("AgentNodeSetupDialog with no eligible agents", () => {
  it("says what to do in one sentence", () => {
    renderEmpty();
    const body = screen.getByTestId("agent-node-setup-body").textContent;
    expect(body).toBe(
      "An Agent node runs a different saved agent, so create and save one in the Agent Playground, then click Refresh agents.",
    );
    expect(body.match(/[.!?](\s|$)/g)).toHaveLength(1);
  });

  // The agent on the canvas never counts, so saving it changes nothing.
  // The sentence must ask for a second agent, not "save one".
  it("asks for a second agent, not for saving this one", () => {
    renderEmpty();
    const body = screen.getByTestId("agent-node-setup-body").textContent;
    expect(body).toMatch(/\bdifferent\b/);
    expect(body).toMatch(/\bcreate\b/);
  });

  it("offers the one action that fixes it, and the button the sentence names", () => {
    renderEmpty();
    const link = screen.getByRole("link", {
      name: "Open Agent Playground in new tab",
    });
    expect(link).toHaveAttribute("href", "/dashboard/agents/playground");
    expect(link).toHaveAttribute("target", "_blank");
    expect(
      screen.getByRole("button", { name: "Refresh agents" }),
    ).toBeInTheDocument();
  });

  it("keeps the title", () => {
    renderEmpty();
    expect(
      screen.getByText("No eligible agents available here"),
    ).toBeInTheDocument();
  });
});
