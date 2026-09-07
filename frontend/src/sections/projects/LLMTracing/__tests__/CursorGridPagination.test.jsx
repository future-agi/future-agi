import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CursorGridPagination from "../CursorGridPagination";

const setup = (props = {}) =>
  render(
    <CursorGridPagination
      page={1}
      pageSize={25}
      hasMore
      provenNext
      onPageChange={vi.fn()}
      onPageSizeChange={vi.fn()}
      {...props}
    />,
  );

const pageButtonLabels = () =>
  screen
    .getAllByRole("button", { name: /^Go to page \d+$/ })
    .map((node) => node.textContent.trim());

describe("CursorGridPagination", () => {
  it("shows page 1 and the proven next page", () => {
    setup({ page: 1, provenNext: true, hasMore: true });
    expect(pageButtonLabels()).toEqual(["1", "2"]);
  });

  it("never renders more than four page numbers", () => {
    setup({ page: 8, provenNext: true, hasMore: true });
    expect(pageButtonLabels()).toEqual(["1", "7", "8", "9"]);
  });

  it("renders a trailing ellipsis while more may exist", () => {
    setup({ page: 8, provenNext: true, hasMore: true });
    expect(screen.getByTestId("pager-trailing-ellipsis")).toBeTruthy();
  });

  it("drops the trailing ellipsis on the terminal page", () => {
    setup({ page: 9, provenNext: false, hasMore: false });
    expect(screen.queryByTestId("pager-trailing-ellipsis")).toBeNull();
    expect(pageButtonLabels()).toEqual(["1", "8", "9"]);
  });

  it("disables Next on the terminal page", () => {
    setup({ page: 9, provenNext: false, hasMore: false });
    expect(screen.getByRole("button", { name: "Next page" }).disabled).toBe(
      true,
    );
  });

  it("disables Back on page 1", () => {
    setup({ page: 1, provenNext: true, hasMore: true });
    expect(screen.getByRole("button", { name: "Previous page" }).disabled).toBe(
      true,
    );
  });

  it("enables Next when more may exist even with no proven page", () => {
    setup({ page: 1, provenNext: false, hasMore: true });
    expect(pageButtonLabels()).toEqual(["1"]);
    expect(screen.getByRole("button", { name: "Next page" }).disabled).toBe(
      false,
    );
  });

  it("moves a page when a number is clicked", () => {
    const onPageChange = vi.fn();
    setup({ page: 8, provenNext: true, onPageChange });
    screen.getByRole("button", { name: "Go to page 9" }).click();
    expect(onPageChange).toHaveBeenCalledWith(9);
  });

  it("moves forward one page when Next is clicked", () => {
    // A middle page pins the arithmetic (page + 1) rather than a boundary
    // where an off-by-one could accidentally read as correct.
    const onPageChange = vi.fn();
    setup({ page: 4, hasMore: true, provenNext: true, onPageChange });
    screen.getByRole("button", { name: "Next page" }).click();
    expect(onPageChange).toHaveBeenCalledWith(5);
  });

  it("moves back one page when Back is clicked", () => {
    const onPageChange = vi.fn();
    setup({ page: 4, hasMore: true, provenNext: true, onPageChange });
    screen.getByRole("button", { name: "Previous page" }).click();
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  // Regression: the Back/Next labels used to be inline arrow functions passed to
  // MUI's `slots`. A new function identity per render is a new component type, so
  // React remounted the label div on every render. While an ancestor re-renders in
  // a loop that destroys the click target between pointerdown and pointerup, and
  // the browser never emits a click — Back/Next looked dead while the page-number
  // buttons (which have no such child) kept working.
  it("keeps the Back/Next label node across re-renders and lets clicks reach the button", () => {
    const { rerender } = setup({ page: 4, hasMore: true, provenNext: true });
    const next = screen.getByRole("button", { name: "Next page" });
    const labelBefore = next.querySelector("div");
    expect(labelBefore).toBeTruthy();

    // any prop change; the label must be reused, not remounted
    rerender(
      <CursorGridPagination
        page={4}
        pageSize={25}
        hasMore
        provenNext
        loading={false}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />,
    );

    const labelAfter = screen
      .getByRole("button", { name: "Next page" })
      .querySelector("div");
    expect(labelAfter).toBe(labelBefore);
    // and the label must not swallow the pointer — the button is the target
    expect(labelAfter.style.pointerEvents || "none").toBe("none");
  });

  // Regression: the trailing ellipsis used to be driven by `hasMore`, which
  // answers "can you move forward from here". After walking to the last page
  // and returning to page 1, `hasMore` is true again (you can go forward), so
  // the ellipsis reappeared and read as if new pages had arrived. It must be
  // driven by whether the END is still unknown, which is a separate question.
  it("hides the trailing ellipsis once the end is known, even where Next is still enabled", () => {
    setup({ page: 1, hasMore: true, provenNext: true, endUnknown: false });
    expect(screen.queryByTestId("pager-trailing-ellipsis")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Next page" }),
    ).not.toBeDisabled();
  });

  it("shows the trailing ellipsis while the end is still unknown", () => {
    setup({ page: 1, hasMore: true, provenNext: true, endUnknown: true });
    expect(screen.getByTestId("pager-trailing-ellipsis")).toBeTruthy();
  });

  it("falls back to hasMore when a consumer passes no endUnknown", () => {
    setup({ page: 1, hasMore: true, provenNext: true });
    expect(screen.getByTestId("pager-trailing-ellipsis")).toBeTruthy();
  });
});
