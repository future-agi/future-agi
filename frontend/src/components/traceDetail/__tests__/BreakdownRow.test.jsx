import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import BreakdownRow from "../EvalRollupSection/BreakdownRow";

const row = { span_id: "span-1", span_name: "LLM call" };

describe("eval breakdown rows", () => {
  it.each([
    ["pass", "pass"],
    ["fail", "fail"],
    [0, "0"],
    [72, "72"],
    [["safe", "helpful"], "safe, helpful"],
  ])(
    "shows the completed result %j rather than its lifecycle status",
    (value, text) => {
      render(<BreakdownRow row={{ ...row, status: "completed", value }} />);
      expect(screen.getByText(text, { exact: true })).toBeVisible();
      expect(
        screen.queryByText("completed", { exact: true }),
      ).not.toBeInTheDocument();
    },
  );

  it.each(["pending", "running"])(
    "shows %s instead of a stale result",
    (status) => {
      render(<BreakdownRow row={{ ...row, status, value: "pass" }} />);
      expect(screen.getByText(status, { exact: true })).toBeVisible();
      expect(
        screen.queryByText("pass", { exact: true }),
      ).not.toBeInTheDocument();
    },
  );

  it("shows an execution error instead of a stale result", () => {
    render(<BreakdownRow row={{ ...row, status: "errored", value: "pass" }} />);
    expect(screen.getByText("Error", { exact: true })).toBeVisible();
    expect(screen.queryByText("pass", { exact: true })).not.toBeInTheDocument();
  });

  it("shows the skip reason and keeps unavailable navigation disabled", () => {
    render(
      <BreakdownRow
        row={{ ...row, status: "skipped", skipped_reason: "Budget exhausted" }}
      />,
    );
    expect(screen.getByText("Budget exhausted")).toBeVisible();
    expect(screen.getByRole("button", { name: "View span" })).toBeDisabled();
  });

  it("navigates to the selected span and expands its explanation", async () => {
    const onSelectSpan = vi.fn();
    const user = userEvent.setup();
    render(
      <BreakdownRow
        row={{
          ...row,
          status: "completed",
          value: "fail",
          explanation: "An unsupported claim.",
        }}
        onSelectSpan={onSelectSpan}
      />,
    );
    await user.click(screen.getByRole("button", { name: "View span" }));
    expect(onSelectSpan).toHaveBeenCalledExactlyOnceWith("span-1");
    await user.click(screen.getByRole("button", { name: "Show explanation" }));
    expect(screen.getByText("An unsupported claim.")).toBeVisible();
  });
});
