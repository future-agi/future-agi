import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import OriginChip from "../components/OriginChip";

describe("OriginChip", () => {
  it("renders the short label for each known origin", () => {
    const { rerender } = render(<OriginChip origin="code" />);
    expect(screen.getByText("CODE")).toBeInTheDocument();
    rerender(<OriginChip origin="policy" />);
    expect(screen.getByText("POLICY.YAML")).toBeInTheDocument();
    rerender(<OriginChip origin="callGraph" />);
    expect(screen.getByText("CALL-GRAPH")).toBeInTheDocument();
    rerender(<OriginChip origin="provider_system_prompt" />);
    expect(screen.getByText("SYSTEM PROMPT")).toBeInTheDocument();
    rerender(<OriginChip origin="provider_tool_list" />);
    expect(screen.getByText("TOOL LIST")).toBeInTheDocument();
  });

  it("renders the origin kinds ALK writes for prompts, tool lists and files", () => {
    const { rerender } = render(<OriginChip origin="system_prompt" />);
    expect(screen.getByText("SYSTEM PROMPT")).toBeInTheDocument();
    rerender(<OriginChip origin="tool_list" />);
    expect(screen.getByText("TOOL LIST")).toBeInTheDocument();
    rerender(<OriginChip origin="file" />);
    expect(screen.getByText("FILE")).toBeInTheDocument();
    rerender(<OriginChip origin="platform_builtin" />);
    expect(screen.getByText("BUILT-IN")).toBeInTheDocument();
  });

  it("renders nothing for an unknown origin", () => {
    const { container } = render(<OriginChip origin="mystery" />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the file:line path when showPath is set", () => {
    render(<OriginChip origin="code" file="agent/policy.py" line={12} />);
    expect(screen.getByText("agent/policy.py:12")).toBeInTheDocument();
  });

  it("shows a provider field without an undefined line suffix", () => {
    render(<OriginChip origin="provider_system_prompt" file="system_prompt" />);
    expect(screen.getByText("system_prompt")).toBeInTheDocument();
    expect(screen.queryByText("system_prompt:undefined")).toBeNull();
  });

  it("hides the file:line path when showPath is false", () => {
    render(<OriginChip origin="code" file="agent/policy.py" line={12} showPath={false} />);
    expect(screen.queryByText("agent/policy.py:12")).toBeNull();
  });

  it("reveals the origin label in a tooltip on hover", async () => {
    const user = userEvent.setup();
    render(<OriginChip origin="code" />);
    await user.hover(screen.getByText("CODE"));
    expect(await screen.findByText("enforced in code")).toBeInTheDocument();
  });
});
