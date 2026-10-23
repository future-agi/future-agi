import React from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: { get: http.get },
  endpoints: { project: { instantEvalRows: () => "/rows" } },
}));

import { ROWS_PAGE, useAskRows } from "../useAskRows";

const page = (start, next) => ({
  status: 200,
  data: {
    status: true,
    result: {
      rows: Array.from({ length: ROWS_PAGE }, (_, i) => ({
        id: `r${start + i}`,
        time: "2026-10-13T14:10:00Z",
        answers: {},
        facets: {},
      })),
      next_cursor: next,
      total: 500,
    },
  },
});

const wrapper = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }) =>
    React.createElement(QueryClientProvider, { client }, children);
  Wrapper.propTypes = { children: PropTypes.node };
  return Wrapper;
};

describe("useAskRows on a live run", () => {
  beforeEach(() => {
    http.get.mockReset();
    http.get.mockImplementation((url, { params }) =>
      Promise.resolve(
        params.cursor === "c1" ? page(ROWS_PAGE, "c2") : page(0, "c1"),
      ),
    );
  });

  it("keeps the pages loaded with load more while answers land", async () => {
    const { result, rerender } = renderHook(
      ({ version }) =>
        useAskRows({
          projectId: "p1",
          taskId: "t1",
          unit: "sessions",
          tz: "UTC",
          version,
        }),
      { wrapper: wrapper(), initialProps: { version: 1 } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(ROWS_PAGE));
    await act(async () => {
      await result.current.loadMore();
    });
    await waitFor(() =>
      expect(result.current.rows).toHaveLength(2 * ROWS_PAGE),
    );
    const reads = http.get.mock.calls.length;
    // A poll later, answers landed: the loaded pages stay.
    rerender({ version: 2 });
    rerender({ version: 3 });
    expect(result.current.rows).toHaveLength(2 * ROWS_PAGE);
    expect(http.get.mock.calls.length).toBe(reads);
    // The run ended: every loaded page is read again, in place.
    rerender({ version: "done" });
    await waitFor(() => expect(http.get.mock.calls.length).toBe(reads + 2));
    expect(result.current.rows).toHaveLength(2 * ROWS_PAGE);
  });
});
