import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ErrorFeedFilters from "../ErrorFeedFilters";

const mutate = vi.fn();

vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useObserveProjectList: () => ({ data: [] }),
  useUpdateErrorFeedIssue: () => ({ mutate }),
}));

// TH-8210: the filter bar sits in the Error Feed page's overflow:hidden content
// box next to the fixed-width dashboard nav. With a non-wrapping row, the search
// box and six filter selects push the selection chip and "Bulk actions" past the
// content edge at common laptop widths (1280-1600px at 100% zoom), where they are
// clipped and cannot be clicked. jsdom cannot measure layout, so these tests pin
// the layout contract that keeps the bulk group inside the row: one wrapping
// row of individual controls, with a bulk group that never shrinks. The
// real-browser width matrix is recorded on the PR.

const rowOf = (a, b) => {
  for (let n = a.parentElement; n; n = n.parentElement) {
    if (n.contains(b)) return n;
  }
  return null;
};

const childOf = (row, el) => {
  let n = el;
  while (n && n.parentElement !== row) n = n.parentElement;
  return n;
};

const renderFilters = (selected, onClearSelection = vi.fn()) =>
  render(
    <ErrorFeedFilters
      selected={selected}
      onClearSelection={onClearSelection}
    />,
  );

describe("ErrorFeedFilters bulk actions (TH-8210)", () => {
  it("puts the filters and Bulk actions in one wrapping row so the button continues on the next line instead of being clipped", () => {
    renderFilters(["cluster-a", "cluster-b"]);

    const search = screen.getByPlaceholderText("Search errors");
    const bulk = screen.getByRole("button", { name: /bulk actions/i });
    const row = rowOf(search, bulk);
    const bulkGroup = childOf(row, bulk);

    expect(row).toHaveStyle({ "flex-wrap": "wrap" });

    // Every filter control is its own item of that row. A nested,
    // non-wrapping group of controls would again be too wide for the row.
    const controls = [search, ...screen.getAllByRole("combobox")];
    expect(controls).toHaveLength(7);
    controls.forEach((control) => {
      const item = childOf(row, control);
      const othersInItem = controls.filter(
        (other) => other !== control && item.contains(other),
      );
      expect(othersInItem).toEqual([]);
    });

    // The bulk group keeps its full width and sits at the right end of
    // whichever line it lands on.
    expect(bulkGroup).toHaveStyle({
      "flex-shrink": "0",
      "margin-left": "auto",
    });
    expect(bulkGroup).toContainElement(screen.getByText("2 selected"));
  });

  it("adds nothing after the filters when no rows are selected", () => {
    const { rerender } = renderFilters(["cluster-a"]);
    const row = rowOf(
      screen.getByPlaceholderText("Search errors"),
      screen.getByRole("button", { name: /bulk actions/i }),
    );

    rerender(<ErrorFeedFilters selected={[]} onClearSelection={vi.fn()} />);

    // An empty trailing group would wrap onto a blank line at narrow widths
    // and push the table down for no reason.
    expect(row.lastElementChild).toHaveTextContent("All Sources");
    expect(
      screen.queryByRole("button", { name: /bulk actions/i }),
    ).not.toBeInTheDocument();
  });

  it("still applies the chosen status to every selected issue", () => {
    mutate.mockClear();
    const onClearSelection = vi.fn();
    renderFilters(["cluster-a", "cluster-b"], onClearSelection);

    fireEvent.click(screen.getByRole("button", { name: /bulk actions/i }));
    fireEvent.click(
      screen.getByRole("menuitem", { name: /mark as acknowledged/i }),
    );

    expect(mutate).toHaveBeenCalledTimes(2);
    expect(mutate).toHaveBeenCalledWith({
      clusterId: "cluster-a",
      status: "acknowledged",
    });
    expect(mutate).toHaveBeenCalledWith({
      clusterId: "cluster-b",
      status: "acknowledged",
    });
    expect(onClearSelection).toHaveBeenCalledTimes(1);
  });
});
