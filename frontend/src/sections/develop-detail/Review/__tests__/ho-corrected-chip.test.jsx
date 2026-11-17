// HO results R4: the grid's Corrected mark. Comfortable rows show the
// labelled chip; the one-line density shows the icon alone at the start of
// the line (its row-hover actions cover the end), named for screen readers.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CorrectedOutputChip from "../CorrectedOutputChip";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const result = {
  status: "completed",
  corrected_output: {
    value: "Refund issued to the original card.",
    by: { id: "u1", name: "Maya Chen" },
    at: "2026-10-24T03:00:00Z",
  },
};

describe("CorrectedOutputChip", () => {
  it("shows the labelled chip on a comfortable row", () => {
    render(<CorrectedOutputChip result={result} />);
    const chip = screen.getByTestId("cell-corrected");
    expect(chip.textContent).toBe("Corrected");
    expect(chip.getAttribute("role")).toBeNull();
  });

  it("is the named icon alone on a compact row", () => {
    render(<CorrectedOutputChip result={result} compact />);
    const chip = screen.getByRole("img");
    expect(chip.textContent).toBe("");
    expect(chip.getAttribute("aria-label")).toMatch(/^Corrected by Maya Chen/);
  });

  it("renders nothing without a corrected output", () => {
    render(<CorrectedOutputChip result={{ status: "completed" }} compact />);
    expect(screen.queryByTestId("cell-corrected")).toBeNull();
  });
});
