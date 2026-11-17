import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";

import FilterPanel from "../FilterPanel";

// A field may name operators it does not offer (`unavailableOperators`). The
// operator menu lists each one turned off, with its reason readable under it,
// while the offered operators stay selectable.
const FIELDS = [
  {
    value: "name",
    label: "Name",
    type: "string",
    operators: ["contains"],
    unavailableOperators: { equals: "Not available in this release" },
  },
];

describe("FilterPanel operator menu", () => {
  it("lists an unavailable operator turned off with its reason, and the offered one selectable", async () => {
    const user = userEvent.setup();
    render(
      <FilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        filterFields={FIELDS}
        currentFilters={null}
        onApply={vi.fn()}
        basicOnly
      />,
    );

    const [, operatorSelect] = screen.getAllByRole("combobox");
    await user.click(operatorSelect);
    const listbox = await screen.findByRole("listbox");

    const off = within(listbox).getByRole("option", { name: /Equals/ });
    expect(off).toHaveAttribute("aria-disabled", "true");
    expect(
      within(off).getByText("Not available in this release"),
    ).toBeInTheDocument();

    const offered = within(listbox).getByRole("option", { name: /Contains/ });
    expect(offered).not.toHaveAttribute("aria-disabled", "true");
  });
});
