import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme } from "@mui/material/styles";
import { render, screen } from "src/utils/test-utils";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: {
    project: { projectObserveList: "/tracer/project/list_projects/" },
  },
}));

vi.mock("./TagEditor", () => ({ default: () => <div>tags</div> }));

import ObserveListView from "./ObserveListView";

const theme = createTheme();

const respondEmpty = () =>
  axiosGetMock.mockResolvedValue({
    data: {
      status: true,
      result: {
        table: [],
        metadata: {
          total_rows: 0,
          total_pages: 0,
          page_number: 0,
          page_size: 25,
        },
      },
    },
  });

describe("ObserveListView while the project count is still loading", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    respondEmpty();
  });

  it("holds an empty list in its loading state instead of the first-run screen", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const onFirstRunChange = vi.fn();
    const list = (countPending) => (
      <QueryClientProvider client={queryClient}>
        <ObserveListView
          countPending={countPending}
          onFirstRunChange={onFirstRunChange}
        />
      </QueryClientProvider>
    );

    const { rerender } = render(list(false), { theme });
    expect(await screen.findByText("No traces yet")).toBeInTheDocument();
    expect(onFirstRunChange).toHaveBeenLastCalledWith(true);

    rerender(list(true));
    expect(screen.queryByText("No traces yet")).not.toBeInTheDocument();
    expect(onFirstRunChange).toHaveBeenLastCalledWith(false);

    rerender(list(false));
    expect(await screen.findByText("No traces yet")).toBeInTheDocument();
  });
});
