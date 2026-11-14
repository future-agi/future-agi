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
    ["kept_as_written", "Kept as written"],
    ["archived", "Archived"],
  ])("labels the %s state", (state, label) => {
    render(<LifecycleBadge row={row({ state, reason: null, reason_text: null })} />);
    expect(screen.getByTestId("lifecycle-badge")).toHaveTextContent(label);
  });

  it("marks a legacy validation and explains it on hover", async () => {
    const user = userEvent.setup();
    render(<LifecycleBadge row={row({ state: "kept_as_written", legacy: true, reason_text: null })} />);
    await user.hover(screen.getByTestId("lifecycle-badge"));
    expect(
      await screen.findByText("Validated before checks were recorded. Validate again to confirm."),
    ).toBeInTheDocument();
  });

  it("reads Kept as written, never Active, for a lane warning and explains it on hover", async () => {
    const user = userEvent.setup();
    render(
      <LifecycleBadge
        row={row({ state: "kept_as_written", warning: "solution_assumed", warning_text: "never ran" })}
      />,
    );
    expect(screen.getByTestId("lifecycle-badge")).toHaveTextContent("Kept as written");
    expect(screen.getByTestId("lifecycle-badge")).not.toHaveTextContent("Active");
    await user.hover(screen.getByTestId("lifecycle-badge"));
    expect(await screen.findByText("never ran")).toBeInTheDocument();
  });

  it("adds a Not measured chip when a check reads evidence the agent does not give", () => {
    const { rerender } = render(
      <LifecycleBadge row={row({ state: "active", warning: "evidence_not_captured", warning_text: "x" })} />,
    );
    expect(screen.getByTestId("lifecycle-not-measured-chip")).toHaveTextContent("Not measured at run time");
    rerender(<LifecycleBadge row={row({ state: "active", warning: null })} />);
    expect(screen.queryByTestId("lifecycle-not-measured-chip")).toBeNull();
  });

  it("shows nothing for an environment without versions", () => {
    render(<LifecycleBadge row={row(null)} />);
    expect(screen.queryByTestId("lifecycle-badge")).toBeNull();
  });
});
