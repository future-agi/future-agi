import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CursorGridPagination from "../CursorGridPagination";

// The footer shows where the page on screen sits in the list ("26–50"), and
// adds "of N" only when the caller passes an exact total the list API
// reported. No icons beyond the component's own Back/Next arrows render here.
const renderPager = (props = {}) => (
  <CursorGridPagination
    page={1}
    pageSize={25}
    hasMore
    provenNext
    onPageChange={vi.fn()}
    onPageSizeChange={vi.fn()}
    {...props}
  />
);

const setup = (props = {}) => render(renderPager(props));

const rangeText = () => screen.queryByTestId("pager-range")?.textContent;

describe("CursorGridPagination visible range", () => {
  it("shows 1–25 on a full first page when the list has no exact total", () => {
    setup({ page: 1, rowCount: 25 });
    expect(screen.getByText("1–25")).toBeInTheDocument();
    expect(rangeText()).toBe("1–25");
  });

  it("adds of N when the caller passes an exact total", () => {
    setup({ page: 2, rowCount: 25, totalCount: 57 });
    expect(screen.getByText("26–50 of 57")).toBeInTheDocument();
  });

  it("ends the range at the last row on a short final page", () => {
    setup({
      page: 3,
      rowCount: 7,
      totalCount: 57,
      hasMore: false,
      provenNext: false,
    });
    expect(screen.getByText("51–57 of 57")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("shows the range without a total, and keeps Next, while the API reports more rows but no total", () => {
    setup({
      page: 1,
      rowCount: 25,
      totalCount: null,
      hasMore: true,
      provenNext: false,
    });
    expect(rangeText()).toBe("1–25");
    expect(screen.queryByText(/ of /)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Next page" }),
    ).not.toBeDisabled();
  });

  it("follows the selected page size", () => {
    setup({ page: 3, pageSize: 50, rowCount: 50 });
    expect(rangeText()).toBe("101–150");
  });

  it("drops a total below the last row on screen instead of printing it", () => {
    setup({ page: 2, rowCount: 25, totalCount: 40 });
    expect(rangeText()).toBe("26–50");
    expect(screen.queryByText(/of 40/)).not.toBeInTheDocument();
  });

  it("removes the range when the rows on the page are no longer known, and draws none for an empty page", () => {
    const { rerender } = setup({ page: 1, rowCount: 25, totalCount: 57 });
    expect(rangeText()).toBe("1–25 of 57");

    // A page navigated to but not yet published: no rows known, no range.
    rerender(renderPager({ page: 2, totalCount: 57 }));
    expect(screen.queryByTestId("pager-range")).not.toBeInTheDocument();

    rerender(renderPager({ page: 1, rowCount: 0, totalCount: 0 }));
    expect(screen.queryByTestId("pager-range")).not.toBeInTheDocument();
  });

  it("keeps the range out of the status role the page loader uses", () => {
    setup({ page: 2, rowCount: 25, totalCount: 57, loading: true });
    expect(rangeText()).toBe("26–50 of 57");
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });
});
