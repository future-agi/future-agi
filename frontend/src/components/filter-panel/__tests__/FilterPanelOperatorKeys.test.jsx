import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, waitFor } from "src/utils/test-utils";

import FilterPanel from "../FilterPanel";

const FIELDS = [
  { value: "name", label: "Scenario", type: "string" },
  {
    value: "use_case",
    label: "Use case",
    type: "enum",
    choices: ["orders", "refunds"],
  },
];

const openWith = (props) =>
  render(
    <FilterPanel
      anchorEl={document.body}
      open
      onClose={vi.fn()}
      filterFields={FIELDS}
      currentFilters={null}
      onApply={vi.fn()}
      basicOnly
      {...props}
    />,
  );

const comboboxText = () =>
  screen.getAllByRole("combobox").map((el) => el.textContent);

describe("FilterPanel operatorKeys", () => {
  it("sends a Contains row under its operator key", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    openWith({ onApply, operatorKeys: true });

    await user.type(screen.getByPlaceholderText("Enter value"), "c");

    await waitFor(
      () => expect(onApply).toHaveBeenCalledWith({ name__contains: ["c"] }),
      { timeout: 2000 },
    );
  });

  it("keeps the bare key for callers that did not opt in", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    openWith({ onApply });

    await user.type(screen.getByPlaceholderText("Enter value"), "c");

    await waitFor(() => expect(onApply).toHaveBeenCalledWith({ name: ["c"] }), {
      timeout: 2000,
    });
  });

  it("reopens each text operator it applied, and stays quiet", async () => {
    const onApply = vi.fn();
    openWith({
      onApply,
      operatorKeys: true,
      currentFilters: {
        name__starts_with: ["track"],
        name__not_contains: ["late"],
        name: ["refund"],
        name_not: ["cancel"],
        use_case: ["orders"],
      },
    });

    const labels = comboboxText();
    expect(labels).toEqual(
      expect.arrayContaining([
        "Starts with",
        "Does not contain",
        "Equals",
        "Not equals",
        "Is",
      ]),
    );
    await new Promise((r) => setTimeout(r, 800));
    expect(onApply).not.toHaveBeenCalled();
  });
});
