import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import OriginChip from "../components/OriginChip";

describe("OriginChip", () => {
  it("renders the short label for each known origin", () => {
    const { rerender } = render(<OriginChip origin="code" />);
    expect(screen.getByText("Code")).toBeInTheDocument();
    rerender(<OriginChip origin="policy" />);
    expect(screen.getByText("policy.yaml")).toBeInTheDocument();
    rerender(<OriginChip origin="callGraph" />);
    expect(screen.getByText("Call-graph")).toBeInTheDocument();
  });

  it("renders nothing for an unknown origin", () => {
    const { container } = render(<OriginChip origin="mystery" />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the file:line path when showPath is set", () => {
    render(<OriginChip origin="code" file="agent/policy.py" line={12} />);
    expect(screen.getByText("agent/policy.py:12")).toBeInTheDocument();
  });

  it("hides the file:line path when showPath is false", () => {
    render(<OriginChip origin="code" file="agent/policy.py" line={12} showPath={false} />);
    expect(screen.queryByText("agent/policy.py:12")).toBeNull();
  });

  it("reveals the origin label in a tooltip on hover", async () => {
    const user = userEvent.setup();
    render(<OriginChip origin="code" />);
    await user.hover(screen.getByText("Code"));
    expect(await screen.findByText("enforced in code")).toBeInTheDocument();
  });
});
