// PF-4 (PUX-8): "Row not found" offers "View version history" (and says
// the history lists deleted rows) only when there is a history to open.
// A launch build hides dataset.versions, so the door would lead nowhere.
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RowNotFound } from "../RowViewStates";

describe("RowNotFound (PF-4)", () => {
  it("without onOpenHistory: no history door and no history sentence", () => {
    render(<RowNotFound onClose={() => {}} />);
    expect(screen.getByText("Row not found")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "View version history" }),
    ).toBeNull();
    expect(screen.queryByText(/version history/i)).toBeNull();
    expect(
      screen.getByText(
        "It may have been deleted, or the link points to another dataset.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
  });

  it("with onOpenHistory: the door and the sentence, and the door opens it", () => {
    const open = vi.fn();
    render(<RowNotFound onOpenHistory={open} onClose={() => {}} />);
    fireEvent.click(
      screen.getByRole("button", { name: "View version history" }),
    );
    expect(open).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText(/The version history lists every deleted row\./),
    ).toBeTruthy();
  });
});
