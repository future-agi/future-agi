import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ErrorFeedView from "../ErrorFeedView";

// Only the page header's error count is under test; the filter bar and the
// table are stubs and the list query is set per case. The Observe project
// catalog has loaded one project, as a real workspace has.
const query = vi.hoisted(() => ({ current: {} }));
const catalog = vi.hoisted(() => ({
  data: [{ value: "p-1", label: "Checkout bot" }],
  isPending: false,
  isLoading: false,
  isSuccess: true,
  isError: false,
  refetch: vi.fn(),
}));

vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorFeedList: () => query.current,
  useObserveProjectList: () => catalog,
}));
vi.mock("../components/ErrorFeedFilters", () => ({ default: () => null }));
vi.mock("../components/ErrorFeedTable", () => ({ default: () => null }));

describe("ErrorFeedView header count", () => {
  it("shows no error count when the list failed to load", () => {
    query.current = {
      data: undefined,
      isPending: false,
      isLoading: false,
      isError: true,
      isPaused: false,
    };

    render(<ErrorFeedView />);

    expect(screen.getByText("Error Feed")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows no error count while the list is loading", () => {
    query.current = {
      data: undefined,
      isPending: true,
      isLoading: true,
      isError: false,
      isPaused: false,
    };

    render(<ErrorFeedView />);

    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows the loaded total, including zero", () => {
    query.current = {
      data: { data: [], total: 0 },
      isPending: false,
      isLoading: false,
      isError: false,
      isPaused: false,
    };

    render(<ErrorFeedView />);

    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
