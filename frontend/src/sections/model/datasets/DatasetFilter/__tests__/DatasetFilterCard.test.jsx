import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import DatasetFilterCard from "../DatasetFilterCard";

describe("DatasetFilterCard", () => {
  it("calls addFilter when the add button is clicked", async () => {
    const addFilter = vi.fn();
    render(<DatasetFilterCard addFilter={addFilter} />);

    // The button is icon-only (no text label), so select it by role. The bug
    // was a no-op `onClick={() => {}}`, so this click asserted nothing fired;
    // it now forwards to the addFilter prop.
    const button = screen.getByRole("button");

    await userEvent.setup().click(button);
    expect(addFilter).toHaveBeenCalledTimes(1);
  });
});
