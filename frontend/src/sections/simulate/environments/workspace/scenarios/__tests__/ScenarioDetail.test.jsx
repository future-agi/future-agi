import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import ScenarioDetail from "../ScenarioDetail";

const row = (subTasks) => ({ id: "sc-1", name: "Refund request", task: "Ask for a refund.", subTasks });

describe("ScenarioDetail sub-goals", () => {
  // The scenarios list endpoint sends sub_goals as plain name strings.
  it("shows each sub-goal's text when the list sends plain strings", () => {
    render(<ScenarioDetail row={row(["identity_verified", "refund_created"])} defaultOpen />);
    expect(screen.getByText("Sub-goals: the moves that settle it (2)")).toBeInTheDocument();
    expect(screen.getByText("identity_verified")).toBeInTheDocument();
    expect(screen.getByText("refund_created")).toBeInTheDocument();
  });

  it("shows each sub-goal's label when they arrive as objects", () => {
    render(<ScenarioDetail row={row([{ id: "a", label: "Verify identity" }])} defaultOpen />);
    expect(screen.getByText("Verify identity")).toBeInTheDocument();
  });

  it("skips empty sub-goals rather than numbering a blank line", () => {
    render(<ScenarioDetail row={row(["identity_verified", "", null])} defaultOpen />);
    expect(screen.getByText("Sub-goals: the moves that settle it (1)")).toBeInTheDocument();
  });
});
