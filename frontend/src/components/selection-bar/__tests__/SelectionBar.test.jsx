import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import SelectionBar from "../SelectionBar";

const renderBar = (disabled) =>
  render(
    <SelectionBar
      count={2}
      onCancel={() => {}}
      actions={[
        {
          label: "Run",
          onClick: () => {},
          disabled,
          tooltip: "Pick scenarios from one batch",
        },
      ]}
    />,
  );

describe("SelectionBar", () => {
  it("lets a keyboard reach a disabled action and hear why", () => {
    renderBar(true);
    const reason = screen.getByLabelText("Run: Pick scenarios from one batch");
    expect(reason).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("adds no extra tab stop for an enabled action", () => {
    renderBar(false);
    expect(
      screen.queryByLabelText("Run: Pick scenarios from one batch"),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
  });
});
