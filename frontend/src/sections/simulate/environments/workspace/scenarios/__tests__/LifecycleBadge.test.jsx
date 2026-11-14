import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";

import { LifecycleBadge } from "../ScenarioTableCells";

const row = (lifecycle) => ({ id: "r1", lifecycle });

describe("LifecycleBadge", () => {
  it.each([
    ["draft", "Draft"],
    ["validated", "Validated"],
    ["active", "Active"],
    ["archived", "Archived"],
  ])("labels the %s state", (state, label) => {
    render(<LifecycleBadge row={row({ state, reason: null, reason_text: null })} />);
    expect(screen.getByTestId("lifecycle-badge")).toHaveTextContent(label);
  });

  it("marks a legacy validation and explains it on hover", async () => {
    const user = userEvent.setup();
    render(<LifecycleBadge row={row({ state: "active", legacy: true, reason_text: null })} />);
    await user.hover(screen.getByTestId("lifecycle-badge"));
    expect(
      await screen.findByText("Validated before checks were recorded. Validate again to confirm."),
    ).toBeInTheDocument();
  });

  it("adds a Kept as written chip for a lane warning, and none otherwise", () => {
    const { rerender } = render(
      <LifecycleBadge row={row({ state: "active", warning: "solution_assumed", warning_text: "never ran" })} />,
    );
    expect(screen.getByTestId("lifecycle-kept-chip")).toHaveTextContent("Kept as written");
    rerender(<LifecycleBadge row={row({ state: "active", warning: "legacy_no_negative_control" })} />);
    expect(screen.queryByTestId("lifecycle-kept-chip")).toBeNull();
  });

  it("shows nothing for an environment without versions", () => {
    render(<LifecycleBadge row={row(null)} />);
    expect(screen.queryByTestId("lifecycle-badge")).toBeNull();
  });
});
