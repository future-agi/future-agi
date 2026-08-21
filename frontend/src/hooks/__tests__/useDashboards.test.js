import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  focusManager,
  MutationCache,
  onlineManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("src/utils/axios", () => ({
  default: mocks,
  endpoints: {
    dashboard: {
      list: "/tracer/dashboard/",
      query: "/tracer/dashboard/query/",
      metrics: "/tracer/dashboard/metrics/",
      filterValues: "/tracer/dashboard/filter_values/",
      widgets: (dashboardId) => `/tracer/dashboard/${dashboardId}/widgets/`,
      widgetDetail: (dashboardId, widgetId) =>
        `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/`,
      widgetQuery: (dashboardId, widgetId) =>
        `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/query/`,
      widgetPreview: (dashboardId) =>
        `/tracer/dashboard/${dashboardId}/widgets/preview/`,
      widgetReorder: (dashboardId) =>
        `/tracer/dashboard/${dashboardId}/widgets/reorder/`,
      widgetDuplicate: (dashboardId, widgetId) =>
        `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/duplicate/`,
    },
  },
}));

import {
  useCreateWidget,
  useUpdateWidget,
  useDeleteWidget,
  useReorderWidgets,
  useDuplicateWidget,
  useDashboardQuery,
  useDashboardMetricsPaginated,
  useWidgetQuery,
  usePreviewQuery,
  useDashboardFilterValues,
  FILTER_VALUE_REQUEST_TIMEOUT_MS,
} from "../useDashboards";

const DASHBOARD_LIST_KEY = ["dashboards", "list"];
const dashboardDetailKey = (id) => ["dashboards", "detail", id];

function createQueryWrapper(queryClient) {
  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }
  QueryWrapper.propTypes = { children: PropTypes.node };
  return QueryWrapper;
}

describe("useDashboards widget mutations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("invalidates both the dashboard list and detail caches after creating a widget", async () => {
    mocks.post.mockResolvedValueOnce({ data: { result: { id: "widget-1" } } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useCreateWidget(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({ dashboardId: "dash-1", data: { type: "chart" } });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: dashboardDetailKey("dash-1"),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: DASHBOARD_LIST_KEY,
    });
  });

  it("invalidates both the dashboard list and detail caches after updating a widget", async () => {
    mocks.patch.mockResolvedValueOnce({ data: { result: {} } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useUpdateWidget(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({
      dashboardId: "dash-1",
      widgetId: "widget-1",
      data: { title: "Renamed" },
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: dashboardDetailKey("dash-1"),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: DASHBOARD_LIST_KEY,
    });
  });

  it("invalidates both the dashboard list and detail caches after deleting a widget", async () => {
    mocks.delete.mockResolvedValueOnce({ data: { result: {} } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useDeleteWidget(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({ dashboardId: "dash-1", widgetId: "widget-1" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: dashboardDetailKey("dash-1"),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: DASHBOARD_LIST_KEY,
    });
  });

  it("invalidates both the dashboard list and detail caches after reordering widgets", async () => {
    mocks.post.mockResolvedValueOnce({ data: { result: {} } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useReorderWidgets(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({
      dashboardId: "dash-1",
      order: ["widget-2", "widget-1"],
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: dashboardDetailKey("dash-1"),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: DASHBOARD_LIST_KEY,
    });
  });

  it("invalidates both the dashboard list and detail caches after duplicating a widget", async () => {
    mocks.post.mockResolvedValueOnce({ data: { result: { id: "widget-2" } } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    const { result } = renderHook(() => useDuplicateWidget(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({ dashboardId: "dash-1", widgetId: "widget-1" });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: dashboardDetailKey("dash-1"),
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: DASHBOARD_LIST_KEY,
    });
  });
});

describe("useDashboardMetricsPaginated", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockReset();
  });

  it("requests the finite catalog without capped custom attributes", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          metrics: [{ name: "latency", category: "system_metric" }],
          total: 1,
          page: 1,
          page_size: 50,
          has_more: false,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const { result } = renderHook(
      () =>
        useDashboardMetricsPaginated({
          search: "latency",
          excludeCustomAttributes: true,
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/tracer/dashboard/metrics/", {
      params: {
        search: "latency",
        exclude_custom_attributes: true,
        page: 1,
        page_size: 50,
      },
    });
  });
});

describe("useDashboardFilterValues bounded-read state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // A bounded gesture deliberately leaves later cursor fixtures unused.
    // Reset queued one-shot implementations so they cannot leak into the next
    // test now that one interaction no longer drains an entire cursor chain.
    mocks.get.mockReset();
  });

  const renderValues = (overrides = {}) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return renderHook(
      () =>
        useDashboardFilterValues({
          metricName: "final_status",
          metricType: "custom_attribute",
          projectIds: ["project-synthetic"],
          source: "traces",
          search: "Rejected",
          ...overrides,
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );
  };

  it("does not turn a degraded value response into a legitimate empty result", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: ["Rejected"],
          query_complete: false,
          query_status: "degraded",
        },
      },
    });
    const { result } = renderValues();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(["Rejected"]);
    expect(result.current.queryReadState).toBe("degraded");
    expect(mocks.get).toHaveBeenCalledWith(
      "/tracer/dashboard/filter_values/",
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: FILTER_VALUE_REQUEST_TIMEOUT_MS,
        params: expect.objectContaining({
          metric_name: "final_status",
          project_ids: "project-synthetic",
          search: "Rejected",
        }),
      }),
    );
  });

  it("reports request failure instead of silently converting it to empty", async () => {
    mocks.get.mockRejectedValue({
      result: "Code: 159 DB::Exception: Timeout exceeded",
    });
    const { result } = renderValues();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toEqual([]);
    expect(result.current.queryReadState).toBe("error");
  });

  it("does not replay cached cursor pages on mount, focus, or reconnect", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [{ value: "retained", type: "string" }],
          query_complete: true,
          query_status: "complete",
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const renderCursorHook = () =>
      renderHook(
        () =>
          useDashboardFilterValues({
            metricName: "final_status",
            metricType: "custom_attribute",
            projectIds: ["project-synthetic"],
            source: "traces",
            search: "Rejected",
            pageSize: 10,
          }),
        { wrapper: createQueryWrapper(queryClient) },
      );

    const first = renderCursorHook();
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    first.unmount();
    const cachedQuery = queryClient.getQueryCache().getAll()[0];
    cachedQuery.setState({ ...cachedQuery.state, dataUpdatedAt: 0 });

    focusManager.setFocused(false);
    onlineManager.setOnline(false);
    const remounted = renderCursorHook();
    await act(async () => Promise.resolve());
    expect(mocks.get).toHaveBeenCalledTimes(1);

    await act(async () => focusManager.setFocused(true));
    await act(async () => onlineManager.setOnline(true));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    remounted.unmount();
  });

  it("paginates with an opaque cursor and deduplicates values across pages", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "completed", label: "completed" }],
            query_complete: false,
            query_status: "sampled",
            query_error_code: "sample_limit",
            has_more: true,
            next_cursor: "opaque-page-2",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [
              { value: "completed", label: "duplicate" },
              { value: "failed", label: "failed" },
            ],
            query_complete: true,
            query_status: "complete",
            has_more: false,
            next_cursor: null,
          },
        },
      });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.queryReadState).toBe("sampled");
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(result.current.data).toEqual([
      { value: "completed", label: "completed" },
      { value: "failed", label: "failed" },
    ]);
    expect(mocks.get).toHaveBeenNthCalledWith(
      2,
      "/tracer/dashboard/filter_values/",
      expect.objectContaining({
        params: expect.objectContaining({
          page_size: 10,
          cursor: "opaque-page-2",
        }),
      }),
    );
  });

  it("follows one empty initial system-metric checkpoint to load Model values", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            browse_status: "continuation",
            has_more: true,
            next_cursor: "older-model-window",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "gpt-4.1", label: "gpt-4.1" }],
            query_complete: true,
            query_status: "complete",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        },
      });

    const { result } = renderValues({
      metricName: "model",
      metricType: "system_metric",
      search: "",
      pageSize: 10,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([
      { value: "gpt-4.1", label: "gpt-4.1" },
    ]);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("older-model-window");
    expect(result.current.hasNextPage).toBe(false);
  });

  it("bounds an empty initial system-metric follow-up to one continuation", async () => {
    for (const cursor of ["older-model-window", "oldest-model-window"]) {
      mocks.get.mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            browse_status: "continuation",
            has_more: true,
            next_cursor: cursor,
          },
        },
      });
    }

    const { result } = renderValues({
      metricName: "model",
      metricType: "system_metric",
      search: "",
      pageSize: 10,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual([]);
    expect(result.current.hasNextPage).toBe(true);
  });

  it("stops after an exact empty terminal page", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "CONVERSATION", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "terminal-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        },
      });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(result.current.data).toEqual([
      { value: "CONVERSATION", type: "string" },
    ]);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("terminal-page");
  });

  it("keeps a duplicate-only continuation behind an explicit next action", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "CONVERSATION", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "duplicate-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "CONVERSATION", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "unique-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "SPAN", type: "string" }],
            query_complete: true,
            query_status: "complete",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        },
      });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(result.current.hasNextPage).toBe(true);
    expect(result.current.data).toEqual([
      { value: "CONVERSATION", type: "string" },
    ]);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(result.current.data).toEqual([
      { value: "CONVERSATION", type: "string" },
      { value: "SPAN", type: "string" },
    ]);
    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("duplicate-page");
    expect(mocks.get.mock.calls[2][1].params.cursor).toBe("unique-page");
  });

  it("finishes the d9d sparse value chain after pages 4-7 are empty", async () => {
    const pageValues = [
      ["page-1-a", "page-1-b", "page-1-c", "page-1-d"],
      ["page-2-a"],
      ["page-3-a", "page-3-b"],
      [],
      [],
      [],
      [],
    ];
    pageValues.forEach((values, index) => {
      const terminal = index === pageValues.length - 1;
      mocks.get.mockResolvedValueOnce({
        data: {
          result: {
            values: values.map((value) => ({ value, type: "string" })),
            query_complete: true,
            query_status: "complete",
            ...(terminal ? { browse_status: "exhausted" } : {}),
            has_more: !terminal,
            next_cursor: terminal ? null : `page-${index + 2}`,
          },
        },
      });
    });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    for (
      let expectedRequests = 2;
      expectedRequests <= 7;
      expectedRequests += 1
    ) {
      await act(async () => result.current.fetchNextPage());
      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledTimes(expectedRequests),
      );
      expect(result.current.hasNextPage).toBe(expectedRequests < 7);
    }
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    // Each explicit Load more action owns exactly one physical page. The full
    // signed chain remains reachable without one browser gesture silently
    // draining every sparse page.
    expect(mocks.get).toHaveBeenCalledTimes(7);
    expect(result.current.data).toHaveLength(7);
    expect(mocks.get.mock.calls[3][1].params.cursor).toBe("page-4");
    expect(mocks.get.mock.calls[6][1].params.cursor).toBe("page-7");
  });

  it("stops a repeated cursor instead of leaving another continuation", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "CONVERSATION", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "repeated-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "repeated-page",
          },
        },
      });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(result.current.data).toEqual([
      { value: "CONVERSATION", type: "string" },
    ]);
    expect(result.current.isError).toBe(false);
    expect(result.current.queryReadState).toBe("degraded");
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });

  it("retries a long stopped cache with one fresh request and retains rows", async () => {
    for (let index = 0; index < 4; index += 1) {
      mocks.get.mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: `older-${index + 1}`, type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: `page-${index + 2}`,
          },
        },
      });
    }
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "older-5", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "fresh", type: "string" }],
            query_complete: true,
            query_status: "complete",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        },
      });

    const { result } = renderValues({ pageSize: 10 });
    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    for (
      let expectedRequests = 2;
      expectedRequests <= 5;
      expectedRequests += 1
    ) {
      await act(async () => result.current.fetchNextPage());
      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledTimes(expectedRequests),
      );
    }
    expect(result.current.cursorChainStopped).toBe(true);

    await act(async () => result.current.retryFreshPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(6));
    expect(mocks.get.mock.calls[5][1].params).not.toHaveProperty("cursor");
    expect(result.current.data.map(({ value }) => value)).toEqual([
      "older-1",
      "older-2",
      "older-3",
      "older-4",
      "older-5",
      "fresh",
    ]);
  });

  it.each([
    { has_more: true },
    { next_cursor: "orphaned-cursor" },
    { has_more: false, next_cursor: "unexpected-cursor" },
  ])(
    "makes malformed cursor metadata retryable instead of claiming exhaustion: %j",
    async (cursorMetadata) => {
      mocks.get.mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "CONVERSATION", type: "string" }],
            query_complete: true,
            query_status: "complete",
            ...cursorMetadata,
          },
        },
      });
      const { result } = renderValues({ pageSize: 10 });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data).toEqual([
        { value: "CONVERSATION", type: "string" },
      ]);
      expect(result.current.hasNextPage).toBe(false);
      expect(result.current.queryReadState).toBe("degraded");
      expect(mocks.get).toHaveBeenCalledTimes(1);
    },
  );

  it("does not follow a cursor consumed inside an earlier fetch action", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "internal-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "CONVERSATION", type: "string" }],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "outer-page",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: "internal-page",
          },
        },
      });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(result.current.hasNextPage).toBe(true);
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(result.current.data).toEqual([
      { value: "CONVERSATION", type: "string" },
    ]);
    expect(result.current.isError).toBe(false);
    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(mocks.get.mock.calls[2][1].params.cursor).toBe("outer-page");
  });

  it("treats exhausted as terminal even when has_more is malformed", async () => {
    mocks.get.mockResolvedValueOnce({
      data: {
        result: {
          values: [],
          query_complete: true,
          query_status: "complete",
          browse_status: "exhausted",
          has_more: true,
          next_cursor: "must-not-be-requested",
        },
      },
    });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("continues after limit_reached when an advancing cursor is present", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "recent", type: "string" }],
            query_complete: true,
            query_status: "complete",
            browse_status: "limit_reached",
            has_more: true,
            next_cursor: "next-bounded-batch",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: [{ value: "older", type: "string" }],
            query_complete: true,
            query_status: "complete",
            browse_status: "exhausted",
            has_more: false,
            next_cursor: null,
          },
        },
      });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(result.current.browseLimitReached).toBe(false);
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));

    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("next-bounded-batch");
    expect(result.current.data).toEqual([
      { value: "recent", type: "string" },
      { value: "older", type: "string" },
    ]);
  });

  it("bounds empty auto-follow and resumes until an exact value arrives", async () => {
    let responseIndex = 0;
    mocks.get.mockImplementation(async () => {
      const current = responseIndex;
      responseIndex += 1;
      if (current >= 4) {
        return {
          data: {
            result: {
              values: [{ value: "eventually-found", type: "string" }],
              query_complete: true,
              query_status: "complete",
              browse_status: "exhausted",
              has_more: false,
              next_cursor: null,
            },
          },
        };
      }
      return {
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            has_more: true,
            next_cursor: `cursor-${current + 1}`,
          },
        },
      };
    });
    const { result } = renderValues({ pageSize: 10 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual([]);
    expect(result.current.hasNextPage).toBe(true);

    for (
      let expectedRequests = 2;
      expectedRequests <= 5;
      expectedRequests += 1
    ) {
      await act(async () => result.current.fetchNextPage());
      await waitFor(() =>
        expect(mocks.get).toHaveBeenCalledTimes(expectedRequests),
      );
      expect(result.current.hasNextPage).toBe(expectedRequests < 5);
    }
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
    expect(mocks.get).toHaveBeenCalledTimes(5);
    expect(result.current.data).toEqual([
      { value: "eventually-found", type: "string" },
    ]);
    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("cursor-1");
    expect(mocks.get.mock.calls[4][1].params.cursor).toBe("cursor-4");
  });

  it("starts a searched result set without reusing the previous cursor", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: ["completed"],
          query_complete: true,
          query_status: "complete",
          has_more: false,
          next_cursor: null,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { rerender } = renderHook(
      ({ search }) =>
        useDashboardFilterValues({
          metricName: "call.status",
          metricType: "custom_attribute",
          projectIds: ["project-synthetic"],
          source: "traces",
          search,
          pageSize: 10,
        }),
      {
        initialProps: { search: "comp" },
        wrapper: createQueryWrapper(queryClient),
      },
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    rerender({ search: "fail" });
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));

    expect(mocks.get.mock.calls[1][1].params).toMatchObject({
      search: "fail",
      page_size: 10,
    });
    expect(mocks.get.mock.calls[1][1].params).not.toHaveProperty("cursor");
  });

  it.each(["tracing", "voice"])(
    "retries one cached failed %s value continuation after rapid re-entry",
    async (surface) => {
      let continuationAttempts = 0;
      mocks.get.mockImplementation((_url, { params }) => {
        if (!params.cursor) {
          return Promise.resolve({
            data: {
              result: {
                values: [{ value: "rejected-old", type: "string" }],
                query_complete: true,
                query_status: "complete",
                browse_status: "continuation",
                has_more: true,
                next_cursor: "value-page-2",
              },
            },
          });
        }
        continuationAttempts += 1;
        if (continuationAttempts === 1) {
          return Promise.reject(new Error("value continuation unavailable"));
        }
        return Promise.resolve({
          data: {
            result: {
              values: [{ value: "rejected", type: "string" }],
              query_complete: true,
              query_status: "complete",
              browse_status: "exhausted",
              has_more: false,
              next_cursor: null,
            },
          },
        });
      });
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const { result, rerender } = renderHook(
        ({ searchGesture }) =>
          useDashboardFilterValues({
            metricName: "prompt_slug",
            metricType: "custom_attribute",
            projectIds: [`project-${surface}`],
            // Voice values and voice list filters are trace-root scoped too.
            source: "traces",
            search: "rejected",
            searchGesture,
            pageSize: 10,
          }),
        {
          initialProps: { searchGesture: "rejected" },
          wrapper: createQueryWrapper(queryClient),
        },
      );

      await waitFor(() => expect(result.current.hasNextPage).toBe(true));
      await act(async () => result.current.fetchNextPage());
      await waitFor(() =>
        expect(result.current.isFetchNextPageError).toBe(true),
      );
      expect(continuationAttempts).toBe(1);

      // The debounced transport key stays `rejected`; only the raw gesture
      // changes. The cached failed c1 must receive one fresh bounded retry.
      rerender({ searchGesture: "" });
      rerender({ searchGesture: "rejected" });
      await waitFor(() =>
        expect(result.current.data).toEqual([
          { value: "rejected-old", type: "string" },
          { value: "rejected", type: "string" },
        ]),
      );

      expect(continuationAttempts).toBe(2);
      expect(
        mocks.get.mock.calls.filter(
          ([, options]) =>
            options.params.search === "rejected" && !options.params.cursor,
        ),
      ).toHaveLength(1);
    },
  );

  it("starts a new property value lookup from cursorless page one", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [],
          query_complete: true,
          query_status: "complete",
          browse_status: "exhausted",
          has_more: false,
          next_cursor: null,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { rerender } = renderHook(
      ({ metricName }) =>
        useDashboardFilterValues({
          metricName,
          metricType: "custom_attribute",
          projectIds: ["project-coletia"],
          source: "traces",
          search: "",
          pageSize: 10,
        }),
      {
        initialProps: { metricName: "prompt_slug" },
        wrapper: createQueryWrapper(queryClient),
      },
    );

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(1));
    rerender({ metricName: "another_attribute" });
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));

    expect(mocks.get.mock.calls[0][1].params.metric_name).toBe("prompt_slug");
    expect(mocks.get.mock.calls[1][1].params.metric_name).toBe(
      "another_attribute",
    );
    expect(mocks.get.mock.calls[1][1].params).not.toHaveProperty("cursor");
  });
});

describe("useDashboardQuery error boundary", () => {
  beforeEach(() => vi.clearAllMocks());

  it("marks rejected dashboard queries as locally handled", async () => {
    const rawError = {
      result: "Code: 159 DB::Exception: Timeout exceeded",
    };
    let failedMutation;
    mocks.post.mockRejectedValue(rawError);
    const queryClient = new QueryClient({
      mutationCache: new MutationCache({
        onError: (_error, _variables, _context, mutation) => {
          failedMutation = mutation;
        },
      }),
      defaultOptions: { mutations: { retry: false } },
    });
    const { result } = renderHook(() => useDashboardQuery(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({ metrics: [{ name: "Latency" }] });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(mocks.post).toHaveBeenCalledWith("/tracer/dashboard/query/", {
      metrics: [{ name: "Latency" }],
      allow_sampled: false,
    });
    expect(failedMutation?.options.meta).toEqual({ errorHandled: true });
  });

  it("only sends the cache-bypass flag for an explicit dashboard refresh", async () => {
    mocks.post.mockResolvedValue({ data: { result: { metrics: [] } } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result } = renderHook(() => useDashboardQuery(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({
      queryConfig: { metrics: [{ name: "Latency" }] },
      refresh: true,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.post).toHaveBeenCalledWith(
      "/tracer/dashboard/query/",
      {
        metrics: [{ name: "Latency" }],
        allow_sampled: false,
      },
      { params: { refresh: true } },
    );
  });

  it("does not bypass the exact snapshot cache while polling", async () => {
    mocks.post.mockResolvedValue({ data: { result: { metrics: [] } } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result } = renderHook(() => useDashboardQuery(), {
      wrapper: createQueryWrapper(queryClient),
    });

    result.current.mutate({
      queryConfig: { metrics: [{ name: "Latency" }] },
      refresh: false,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.post).toHaveBeenCalledWith("/tracer/dashboard/query/", {
      metrics: [{ name: "Latency" }],
      allow_sampled: false,
    });
  });

  it("forwards saved-widget cancellation to the dashboard transport", async () => {
    mocks.post.mockResolvedValue({ data: { result: { metrics: [] } } });
    const queryClient = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result } = renderHook(() => useDashboardQuery(), {
      wrapper: createQueryWrapper(queryClient),
    });
    const controller = new AbortController();

    result.current.mutate({
      queryConfig: { metrics: [{ name: "Latency" }] },
      refresh: false,
      signal: controller.signal,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.post).toHaveBeenCalledWith(
      "/tracer/dashboard/query/",
      {
        metrics: [{ name: "Latency" }],
        allow_sampled: false,
      },
      { signal: controller.signal },
    );
  });

  it.each([
    [
      "saved widget",
      useWidgetQuery,
      { dashboardId: "dash-1", widgetId: "widget-1" },
      "/tracer/dashboard/dash-1/widgets/widget-1/query/",
      { allow_sampled: false },
    ],
    [
      "widget preview",
      usePreviewQuery,
      {
        dashboardId: "dash-1",
        queryConfig: { metrics: [{ name: "Latency" }] },
      },
      "/tracer/dashboard/dash-1/widgets/preview/",
      {
        query_config: { metrics: [{ name: "Latency" }] },
        allow_sampled: false,
      },
    ],
  ])(
    "marks rejected %s queries as locally handled",
    async (_, hook, variables, url, body) => {
      let failedMutation;
      mocks.post.mockRejectedValue({
        result: "Code: 159 DB::Exception: Timeout exceeded",
      });
      const queryClient = new QueryClient({
        mutationCache: new MutationCache({
          onError: (_error, _variables, _context, mutation) => {
            failedMutation = mutation;
          },
        }),
        defaultOptions: { mutations: { retry: false } },
      });
      const { result } = renderHook(() => hook(), {
        wrapper: createQueryWrapper(queryClient),
      });

      result.current.mutate(variables);

      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(mocks.post).toHaveBeenCalledWith(url, body);
      expect(failedMutation?.options.meta).toEqual({ errorHandled: true });
    },
  );
});
