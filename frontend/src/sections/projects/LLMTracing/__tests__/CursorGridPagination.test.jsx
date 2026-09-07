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
});
