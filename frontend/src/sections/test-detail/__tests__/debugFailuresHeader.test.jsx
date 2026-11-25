import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import FixMyAgentHeader from "../FixMyAgentDrawer/FixMyAgentHeader";

describe("the Debug failures header", () => {
  it("says what the panel lists", () => {
    render(<FixMyAgentHeader onClose={vi.fn()} />);
    expect(
      screen.getByText("The top issues in this run, and how to fix them."),
    ).toBeInTheDocument();
  });
});
