// "Where the gap is" with a run that has nothing to count (no evaluation
// with a Pass/fail result): each slice line and its ⋯ menu stay, with `—`
// where a figure was, a title that claims no direction, and "Test a fix on
// this slice…" turned off with what Success needs when the run it fixes is
// that run.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "src/utils/test-utils";
import { SUCCESS_NEEDS_PASS_FAIL } from "src/sections/common/Runs/runNumbers";
import { gapSlices } from "src/sections/common/Analysis/runStats";
import GapSlices from "../GapSlices";

vi.mock("src/components/iconify", () => ({ default: () => null }));
// The tooltip's text is drawn next to what it wraps, so a test can read it.
vi.mock("src/components/tooltip", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ title, children }) =>
      createElement(
        "span",
        null,
        children,
        createElement("span", { "data-testid": "tip" }, title),
      ),
  };
});

const NONE = "run-none";
const BOTH = "run-both";
// Eight rows, four per category. NONE errored on every try; BOTH passed.
const MATRIX = {
  rows: [1, 2, 3, 4, 5, 6, 7, 8].map((n) => ({
    row_id: `row-${n}`,
    n,
    group_values: { category: n <= 4 ? "refund" : "status" },
    cells: {
      [NONE]: { trials: ["error", "error"] },
      [BOTH]: { trials: [true, true] },
    },
  })),
  columns: {
    [NONE]: { trials: 2, pass_fail_evals: 0 },
    [BOTH]: { trials: 2, pass_fail_evals: 1 },
  },
};
const KEYS = { keys: ["category"] };

afterEach(cleanup);

const mount = (slices, cand, base, onIterate = vi.fn()) =>
  render(
    <GapSlices
      slices={slices}
      candLabel={cand}
      baseLabel={base}
      candidates={[]}
      onCand={vi.fn()}
      onPick={vi.fn()}
      onShowRows={vi.fn()}
      onIterate={onIterate}
      iterateDisabled={null}
    />,
  );
const menuOf = (i) => {
  fireEvent.click(screen.getAllByLabelText("Slice actions")[i]);
  return screen.getAllByRole("menuitem");
};

describe("the gap box with a baseline that has nothing to count", () => {
  const slices = gapSlices(MATRIX, NONE, BOTH, KEYS);

  it("keeps each slice line, with — for its figures", () => {
    mount(slices, "#2", "#1");
    expect(screen.getByText("#2 vs #1 by slice")).toBeInTheDocument();
    expect(
      screen.getByText("Category refund · — · 4 rows (— → 8/8)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Category status · — · 4 rows (— → 8/8)"),
    ).toBeInTheDocument();
  });

  it("keeps both actions on, as the run it fixes has a figure", () => {
    const onIterate = vi.fn();
    mount(slices, "#2", "#1", onIterate);
    const items = menuOf(0);
    expect(items.map((i) => i.textContent)).toEqual([
      "Show rows",
      "Test a fix on this slice…",
    ]);
    expect(items[1]).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(items[1]);
    expect(onIterate).toHaveBeenCalledWith(slices[0]);
  });
});

describe("the gap box with a candidate that has nothing to count", () => {
  const slices = gapSlices(MATRIX, BOTH, NONE, KEYS);

  it("keeps Test a fix on the slice, turned off with what Success needs", () => {
    const onIterate = vi.fn();
    mount(slices, "#1", "#2", onIterate);
    expect(
      screen.getByText("Category refund · — · 4 rows (8/8 → —)"),
    ).toBeInTheDocument();
    const items = menuOf(0);
    expect(items.map((i) => i.textContent)).toEqual([
      "Show rows",
      "Test a fix on this slice…",
    ]);
    expect(items[0]).not.toHaveAttribute("aria-disabled", "true");
    expect(items[1]).toHaveAttribute("aria-disabled", "true");
    const tips = screen.getAllByTestId("tip").map((t) => t.textContent);
    expect(tips).toContain(SUCCESS_NEEDS_PASS_FAIL);
  });
});
