// HO results R1: the Sorted-by chip. Full on a wide single-run toolbar,
// `out_len · #2B` on the narrow one, the arrow and ✕ alone on the compare
// toolbar (no room beside its pinned actions); the accessible name always
// carries the whole sentence and ✕ goes back to the default order.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomSortChip from "../CustomSortChip";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
vi.mock("../useCustomColumns", () => ({
  default: () => ({
    columns: [{ id: "c1", name: "out_len", applies_to: "run" }],
  }),
}));

const runs = [{ run_id: "b", label: "#2B" }];
const sort = "custom:c1:b:desc";

describe("CustomSortChip", () => {
  it("says the whole sentence on a wide toolbar", () => {
    render(<CustomSortChip datasetId="d" sort={sort} runs={runs} />);
    expect(screen.getByTestId("results-custom-sort").textContent).toContain(
      "Sorted by out_len ↓ · #2B",
    );
  });

  it("is the column and run on the narrow toolbar", () => {
    render(<CustomSortChip datasetId="d" sort={sort} runs={runs} compact />);
    const chip = screen.getByTestId("results-custom-sort");
    expect(chip.textContent).toContain("out_len · #2B");
    expect(chip.getAttribute("aria-label")).toBe("Sorted by out_len ↓ · #2B");
  });

  it("keeps the sentence as its name when tight, and ✕ clears", () => {
    const onClear = vi.fn();
    render(
      <CustomSortChip
        datasetId="d"
        sort={sort}
        runs={runs}
        compact
        tight
        onClear={onClear}
      />,
    );
    const chip = screen.getByTestId("results-custom-sort");
    expect(chip.getAttribute("aria-label")).toBe("Sorted by out_len ↓ · #2B");
    fireEvent.click(chip.querySelector(".MuiChip-deleteIcon"));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("renders nothing for another sort", () => {
    render(<CustomSortChip datasetId="d" sort="regressed" runs={runs} />);
    expect(screen.queryByTestId("results-custom-sort")).toBeNull();
  });
});
