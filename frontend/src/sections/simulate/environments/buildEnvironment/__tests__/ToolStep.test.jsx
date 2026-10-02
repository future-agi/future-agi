import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { Step } from "../console/ConsoleTurn";

const RESULT =
  '{"completed_scenarios": 0, "failed_scenarios": 0, "job_id": "a52f0945-cf58-411b-8c05-b44d572385ad", "receipts": [], "stage": "generating_environment", "state": "provisioning", "total_scenarios": 1}';

const tool = (over = {}) => ({
  id: "tool-1",
  kind: "tool",
  label: "read run",
  state: "completed",
  result: RESULT,
  ...over,
});

// The row is named by the tool alone, not the whole result preview.
const toggle = () => screen.getByRole("button", { name: "read run" });

describe("tool step", () => {
  it("shows a one-line preview and hides the full output until expanded", () => {
    render(<Step step={tool()} />);
    expect(screen.getByText(RESULT)).toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("tool-step-output")).not.toBeInTheDocument();
  });

  it("expands on click to the full output, pretty-printed when it is JSON", () => {
    render(<Step step={tool()} />);
    fireEvent.click(toggle());

    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    const output = screen.getByTestId("tool-step-output");
    expect(output.textContent).toBe(JSON.stringify(JSON.parse(RESULT), null, 2));
    expect(output.textContent).toContain('"state": "provisioning"');
  });

  it("collapses again on a second click", () => {
    render(<Step step={tool()} />);
    fireEvent.click(toggle());
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("toggles from the keyboard", () => {
    render(<Step step={tool()} />);
    fireEvent.keyDown(toggle(), { key: "Enter" });
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(toggle(), { key: " " });
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("shows non-JSON output as written", () => {
    render(<Step step={tool({ result: "explain_monthly_billing passes when the agent declines" })} />);
    fireEvent.click(toggle());
    expect(screen.getByTestId("tool-step-output").textContent).toBe(
      "explain_monthly_billing passes when the agent declines",
    );
  });

  it("is not expandable while running or without output", () => {
    const { rerender } = render(<Step step={tool({ state: "running", result: undefined })} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("…")).toBeInTheDocument();

    rerender(<Step step={tool({ result: "" })} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
