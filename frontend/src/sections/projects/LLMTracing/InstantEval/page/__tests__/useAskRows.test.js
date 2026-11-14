import React from "react";
import PropTypes from "prop-types";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("src/utils/axios", () => ({
  default: http,
  endpoints: {
    project: {
      instantEvalRows: () => "/rows",
      instantEvalExport: () => "/export",
    },
  },
}));

import {
  ROWS_PAGE,
  downloadRowsCsv,
  fetchRowIds,
  useAskRows,
} from "../useAskRows";

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

const wrapper = (queries = {}) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, ...queries } },
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

describe("useAskRows over a chart's selection", () => {
  // "Show" on a 6-9 AM pattern over 30 days: one group key per day, and a
  // model name holding a comma.
  const groups = [
    ...Array.from(
      { length: 30 },
      (_, i) => `hour3:2026-09-${String(i + 1).padStart(2, "0")}|6`,
    ),
    "model:gpt-4o, mini",
  ];
  const sent = (call) => {
    const [, options] = call;
    return new URLSearchParams(
      options.paramsSerializer.serialize(options.params),
    );
  };

  beforeEach(() => {
    http.get.mockReset();
    http.get.mockResolvedValue(page(0, null));
  });

  it("sends each group whole, as its own parameter", async () => {
    renderHook(
      () =>
        useAskRows({
          projectId: "p1",
          taskId: "t1",
          unit: "traces",
          tz: "UTC",
          filter: { group: groups },
          version: "done",
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(http.get).toHaveBeenCalledTimes(1));
    expect(sent(http.get.mock.calls[0]).getAll("group")).toEqual(groups);
  });

  it("hands over the same selection", async () => {
    await fetchRowIds({
      projectId: "p1",
      taskId: "t1",
      tz: "UTC",
      filter: { group: groups },
      limit: 10,
      unit: "traces",
    });
    expect(sent(http.get.mock.calls[0]).getAll("group")).toEqual(groups);
  });

  it.each([
    // A day per pattern match: well beyond URL and Django field limits.
    ["many groups", Array.from({ length: 1100 }, (_, i) => `user:user-${i}`)],
    // Far fewer than 1,000 fields, but over 8 KB after URL encoding.
    ["long encoded keys", Array(40).fill(`model:${"模型, ".repeat(15)}`)],
  ])("posts %s as JSON while paging the table", async (_, selection) => {
    http.post.mockReset();
    http.post.mockImplementation((url, body) =>
      Promise.resolve(body.cursor ? page(ROWS_PAGE, null) : page(0, "c1")),
    );
    const { result } = renderHook(
      () =>
        useAskRows({
          projectId: "p1",
          taskId: "t1",
          unit: "traces",
          tz: "UTC",
          filter: { group: selection },
          version: "done",
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(ROWS_PAGE));
    await act(async () => result.current.loadMore());
    await waitFor(() =>
      expect(result.current.rows).toHaveLength(2 * ROWS_PAGE),
    );
    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).toHaveBeenCalledTimes(2);
    expect(http.post.mock.calls[0]).toEqual([
      "/rows",
      {
        project_id: "p1",
        eval_task_id: "t1",
        tz: "UTC",
        sort: "-time",
        page_size: ROWS_PAGE,
        group: selection,
      },
      { signal: expect.any(AbortSignal) },
    ]);
    expect(http.post.mock.calls[1][1]).toMatchObject({
      group: selection,
      cursor: "c1",
    });
  });

  it("hands a large selection to Insights using JSON on every page", async () => {
    const selection = Array.from({ length: 1100 }, (_, i) => `user:user-${i}`);
    http.post.mockReset();
    http.post
      .mockResolvedValueOnce(page(0, "c1"))
      .mockResolvedValueOnce(page(ROWS_PAGE, null));
    const rows = await fetchRowIds({
      projectId: "p1",
      taskId: "t1",
      tz: "UTC",
      filter: { group: selection, error: false },
      limit: 2 * ROWS_PAGE,
      unit: "traces",
    });
    expect(rows).toHaveLength(2 * ROWS_PAGE);
    expect(http.get).not.toHaveBeenCalled();
    expect(http.post).toHaveBeenCalledTimes(2);
    expect(http.post.mock.calls[1]).toEqual([
      "/rows",
      expect.objectContaining({
        group: selection,
        error: false,
        compact: true,
        page_size: ROWS_PAGE,
        cursor: "c1",
      }),
      {},
    ]);
  });

  it.each([false, true])("downloads CSV with a %s large selection", async (large) => {
    const selection = large ? Array(1100).fill("model:model, with comma") : groups;
    const blob = new Blob(["id,answer\nr1,Yes"], { type: "text/csv" });
    http.post.mockReset();
    http.post.mockResolvedValue({ data: blob });
    http.get.mockResolvedValue({ data: blob });
    const create = vi.fn(() => "blob:csv");
    const revoke = vi.fn();
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = create;
    URL.revokeObjectURL = revoke;
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function () {
        expect(this.href).toBe("blob:csv");
        expect(this.download).toBe("selection.csv");
      });
    vi.useFakeTimers();
    try {
      await downloadRowsCsv({
        projectId: "p1",
        taskId: "t1",
        tz: "UTC",
        filter: { group: selection },
        sessions: true,
        name: "selection.csv",
      });
      if (large) {
        expect(http.get).not.toHaveBeenCalled();
        expect(http.post).toHaveBeenCalledWith(
          "/export",
          expect.objectContaining({ group: selection, sessions: true }),
          { responseType: "blob" },
        );
      } else {
        expect(http.post).not.toHaveBeenCalled();
        expect(sent(http.get.mock.calls[0]).getAll("group")).toEqual(groups);
        expect(http.get.mock.calls[0][1].responseType).toBe("blob");
      }
      expect(create).toHaveBeenCalledWith(blob);
      expect(click).toHaveBeenCalledTimes(1);
      expect(document.querySelector("a[download]")).toBeNull();
      vi.advanceTimersByTime(1000);
      expect(revoke).toHaveBeenCalledWith("blob:csv");
    } finally {
      click.mockRestore();
      URL.createObjectURL = originalCreate;
      URL.revokeObjectURL = originalRevoke;
      vi.useRealTimers();
    }
  });

  it("says a refused read failed, without retrying it", async () => {
    http.get.mockRejectedValue({ statusCode: 400, message: "Bad group" });
    const { result } = renderHook(
      () =>
        useAskRows({
          projectId: "p1",
          taskId: "t1",
          unit: "traces",
          tz: "UTC",
          filter: { group: groups },
          version: "done",
        }),
      { wrapper: wrapper() },
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(http.get).toHaveBeenCalledTimes(1);
  });

  it("doesn't read a table hidden behind an id selection as answers land", async () => {
    const { rerender } = renderHook(
      ({ version }) =>
        useAskRows({
          projectId: "p1",
          taskId: "t1",
          unit: "traces",
          tz: "UTC",
          version,
          enabled: false,
        }),
      { wrapper: wrapper(), initialProps: { version: 1 } },
    );
    rerender({ version: 2 });
    rerender({ version: "done" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(http.get).not.toHaveBeenCalled();
  });

  it("reads the finished run once the hidden table shows again", async () => {
    const rowsPage = (n) => ({
      status: 200,
      data: {
        status: true,
        result: {
          rows: Array.from({ length: n }, (_, i) => ({
            id: `r${i}`,
            time: "2026-10-13T14:10:00Z",
            answers: {},
            facets: {},
          })),
          next_cursor: null,
          total: n,
        },
      },
    });
    http.get.mockResolvedValue(rowsPage(3));
    const { result, rerender } = renderHook(
      ({ version, enabled }) =>
        useAskRows({
          projectId: "p1",
          taskId: "t1",
          unit: "traces",
          tz: "UTC",
          version,
          enabled,
        }),
      {
        // The app's default: rows read under 5 s ago aren't read again just
        // because the query is enabled again.
        wrapper: wrapper({ staleTime: 5000 }),
        initialProps: { version: 3, enabled: true },
      },
    );
    await waitFor(() => expect(result.current.total).toBe(3));
    http.get.mockResolvedValue(rowsPage(10));
    // A chart's id selection hides the table, and the run ends meanwhile.
    rerender({ version: 3, enabled: false });
    rerender({ version: "done", enabled: false });
    rerender({ version: "done", enabled: true });
    await waitFor(() => expect(result.current.total).toBe(10));
    expect(result.current.rows).toHaveLength(10);
  });
});
