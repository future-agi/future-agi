import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ErrorFeedFilters from "../ErrorFeedFilters";

// The filter row sits in a box with overflow: hidden (ErrorFeedView). With the
// search box and six fixed-width selects in one row, the selection chip at the
// end is pushed past the edge and "N selected" is cut off unless the row can
// wrap onto a second line.
vi.mock("src/api/errorFeed/error-feed", () => ({
  useObserveProjectList: () => ({ data: [] }),
  useUpdateErrorFeedIssue: () => ({ mutate: vi.fn() }),
}));

const ids = (n) => Array.from({ length: n }, (_, i) => `cluster-${i + 1}`);

// Nearest element that holds both the search box and the selection chip.
function sharedRow(a, b) {
  let node = a.parentElement;
  while (node && !node.contains(b)) node = node.parentElement;
  return node;
}

function expectChipCanWrapIntoView(count) {
  render(<ErrorFeedFilters selected={ids(count)} onClearSelection={vi.fn()} />);

  const label = screen.getByText(`${count} selected`);
  expect(label.textContent).toBe(`${count} selected`);

  const search = screen.getByPlaceholderText("Search errors");
  const row = sharedRow(search, label);
  const style = window.getComputedStyle(row);
  // Either the toolbar wraps, or the chip lives on its own line.
  const fits =
    style.flexWrap === "wrap" || style.flexDirection === "column";
  expect(
    fits,
    `toolbar row is flex-direction ${style.flexDirection}, flex-wrap ${style.flexWrap || "nowrap"}`,
  ).toBe(true);
}

describe("ErrorFeedFilters selected count", () => {
  it("keeps '47 selected' visible instead of pushing it off the toolbar", () => {
    expectChipCanWrapIntoView(47);
  });

  it("keeps a three digit count visible too", () => {
    expectChipCanWrapIntoView(123);
  });
});
