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
  usePropertyCatalog,
  validatePropertyCatalogPage,
  useWidgetQuery,
  usePreviewQuery,
  useDashboardFilterValues,
  useDatasetColumnValues,
  buildFilterValueRetryScope,
  buildPropertyRegistryId,
  boundPropertyCatalogSearch,
  FILTER_VALUE_REQUEST_TIMEOUT_MS,
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
} from "../useDashboards";

describe("property catalog search contract", () => {
  it("bounds multibyte searches without splitting a code point", () => {
    const search = boundPropertyCatalogSearch("é".repeat(400));

    expect(new TextEncoder().encode(search)).toHaveLength(512);
    expect(search).toBe("é".repeat(256));
  });
});

describe("filter value retry identity", () => {
  const baseScope = {
    propertyId: "custom_attribute:customer.plan",
    metricName: "customer.plan",
    metricType: "custom_attribute",
    projectIds: ["project-1"],
    datasetId: "dataset-1",
    source: "traces",
    workflow: "trace",
    pageSize: 20,
    attributeType: "string",
  };

  it("separates retries by property_id and dataset_id", () => {
    const identity = buildFilterValueRetryScope(baseScope);

    expect(
      buildFilterValueRetryScope({
        ...baseScope,
        propertyId: "custom_attribute:customer.segment",
      }),
    ).not.toBe(identity);
    expect(
      buildFilterValueRetryScope({
        ...baseScope,
        datasetId: "dataset-2",
      }),
    ).not.toBe(identity);
  });
});

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
      signal: expect.anything(),
      timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
      params: {
        search: "latency",
        exclude_custom_attributes: true,
        page: 1,
        page_size: 50,
      },
    });
  });

  it("keys cached catalog pages by page size", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          metrics: [],
          total: 0,
          page: 1,
          has_more: false,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = createQueryWrapper(queryClient);

    const first = renderHook(
      () => useDashboardMetricsPaginated({ pageSize: 25 }),
      { wrapper },
    );
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));

    const second = renderHook(
      () => useDashboardMetricsPaginated({ pageSize: 100 }),
      { wrapper },
    );
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true));

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(
      mocks.get.mock.calls.map(([, config]) => config.params.page_size),
    ).toEqual([25, 100]);
  });

  it("scopes legacy compatibility reads by canonical projects and eval config", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          metrics: [],
          total: 0,
          page: 1,
          page_size: 20,
          has_more: false,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = createQueryWrapper(queryClient);

    const first = renderHook(
      () =>
        useDashboardMetricsPaginated({
          category: "custom_attribute",
          source: "traces",
          search: "prompt_sl",
          projectIds: ["project-b", "project-a", "project-b"],
          perEvalConfig: true,
          pageSize: 20,
        }),
      { wrapper },
    );
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));

    const second = renderHook(
      () =>
        useDashboardMetricsPaginated({
          category: "custom_attribute",
          source: "traces",
          search: "prompt_sl",
          projectIds: ["project-a", "project-b"],
          perEvalConfig: true,
          pageSize: 20,
        }),
      { wrapper },
    );
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true));

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith("/tracer/dashboard/metrics/", {
      signal: expect.anything(),
      timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
      params: {
        category: "custom_attribute",
        source: "traces",
        search: "prompt_sl",
        project_ids: "project-a,project-b",
        per_eval_config: true,
        page: 1,
        page_size: 20,
      },
    });
  });
});

describe("usePropertyCatalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockReset();
  });

  const page = (overrides = {}) => ({
    metrics: [
      {
        name: "customer.plan",
        property_id: "custom_attribute:customer.plan",
        property_kind: "custom_attribute",
        category: "custom_attribute",
      },
    ],
    total: null,
    total_is_exact: false,
    category_counts: {
      all: 1,
      system_metric: 0,
      eval_metric: 0,
      annotation_metric: 0,
      custom_attribute: 1,
      custom_column: 0,
    },
    category_counts_exact: true,
    page_size: 50,
    has_more: false,
    next_cursor: null,
    catalog_epoch: 3,
    catalog_revision: 17,
    activation_fingerprint: "a".repeat(64),
    query_complete: true,
    query_exact: true,
    query_status: "complete",
    query_provenance: "activated_property_catalog",
    ...overrides,
  });

  it("reports a cached remote search refetch as pending", async () => {
    let resolveRefetch;
    const refetchResponse = new Promise((resolve) => {
      resolveRefetch = resolve;
    });
    mocks.get
      .mockResolvedValueOnce({ data: { result: page() } })
      .mockImplementationOnce(() => refetchResponse);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () => usePropertyCatalog({ search: "customer" }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.isRemoteCatalogSearchPending).toBe(false);

    let pendingRefetch;
    act(() => {
      pendingRefetch = result.current.refetch();
    });
    await waitFor(() => expect(result.current.isFetching).toBe(true));
    expect(result.current.isLoading).toBe(false);
    expect(result.current.isRemoteCatalogSearchPending).toBe(true);
    expect(result.current.isRemoteCatalogNextPagePending).toBe(false);

    resolveRefetch({ data: { result: page() } });
    await act(async () => pendingRefetch);
    await waitFor(() =>
      expect(result.current.isRemoteCatalogSearchPending).toBe(false),
    );
  });

  it("reports a remote cursor page independently from search loading", async () => {
    let resolveNextPage;
    const nextPageResponse = new Promise((resolve) => {
      resolveNextPage = resolve;
    });
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockImplementationOnce(() => nextPageResponse);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () => usePropertyCatalog({ search: "customer" }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));

    let pendingNextPage;
    act(() => {
      pendingNextPage = result.current.fetchNextPage();
    });
    await waitFor(() => expect(result.current.isFetchingNextPage).toBe(true));
    expect(result.current.isRemoteCatalogSearchPending).toBe(false);
    expect(result.current.isRemoteCatalogNextPagePending).toBe(true);

    resolveNextPage({ data: { result: page() } });
    await act(async () => pendingNextPage);
    await waitFor(() =>
      expect(result.current.isRemoteCatalogNextPagePending).toBe(false),
    );
  });

  it("walks one signed immutable catalog without page numbers", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockResolvedValueOnce({
        data: {
          result: page({
            metrics: [
              {
                name: "customer.tier",
                property_id: "custom_attribute:customer.tier",
                property_kind: "custom_attribute",
                category: "custom_attribute",
              },
            ],
          }),
        },
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () =>
        usePropertyCatalog({
          category: "custom_attribute",
          search: "customer",
          role: "dimension",
          projectIds: ["project-b", "project-a", "project-a"],
          pageSize: 50,
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.get.mock.calls[0][1].params).toEqual({
      cursor_mode: true,
      page_size: 50,
      category: "custom_attribute",
      search: "customer",
      role: "dimension",
      project_ids: "project-a,project-b",
    });
    expect(mocks.get.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        signal: expect.anything(),
        timeout: PROPERTY_CATALOG_REQUEST_TIMEOUT_MS,
      }),
    );

    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("cursor-2");
    await waitFor(() => expect(result.current.metrics).toHaveLength(2));
    expect(result.current.metrics.map((metric) => metric.property_id)).toEqual([
      "custom_attribute:customer.plan",
      "custom_attribute:customer.tier",
    ]);
    expect(result.current.total).toBeNull();
    expect(result.current.totalIsExact).toBe(false);
    expect(result.current.categoryCounts).toEqual({
      all: 1,
      system_metric: 0,
      eval_metric: 0,
      annotation_metric: 0,
      custom_attribute: 1,
      custom_column: 0,
    });
    expect(result.current.categoryCountsExact).toBe(true);
    expect(result.current.cursorChainStopped).toBe(false);
  });

  it("starts a fresh first page when a picker cache scope changes", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockResolvedValueOnce({
        data: {
          result: page({
            metrics: [
              {
                name: "customer.tier",
                property_id: "custom_attribute:customer.tier",
                property_kind: "custom_attribute",
                category: "custom_attribute",
              },
            ],
          }),
        },
      })
      .mockResolvedValueOnce({ data: { result: page() } });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, rerender } = renderHook(
      ({ cacheScopeKey }) => usePropertyCatalog({ cacheScopeKey }),
      {
        initialProps: { cacheScopeKey: "picker-1" },
        wrapper: createQueryWrapper(queryClient),
      },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.metrics).toHaveLength(2));

    rerender({ cacheScopeKey: "picker-2" });

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.metrics).toHaveLength(1));
    expect(result.current.data.pages).toHaveLength(1);
    expect(result.current.data.pageParams).toEqual([null]);
    expect(mocks.get.mock.calls[2][1].params).not.toHaveProperty("cursor");
  });

  it("stops a malformed or repeated continuation", () => {
    const malformed = validatePropertyCatalogPage(
      page({ has_more: true, next_cursor: "cursor-repeat" }),
      new Set(["cursor-repeat"]),
    );
    const partial = validatePropertyCatalogPage(
      page({ has_more: false, next_cursor: "unexpected" }),
    );

    expect(malformed.__propertyCatalogCursorStopped).toBe("malformed_cursor");
    expect(partial.__propertyCatalogCursorStopped).toBe("malformed_cursor");
  });

  it("rejects a response that does not prove one activated exact revision", () => {
    const degraded = validatePropertyCatalogPage(
      page({ query_complete: false }),
    );

    expect(degraded.__propertyCatalogCursorStopped).toBe("malformed_page");
  });

  it("accepts a pre-count activated page during a rolling backend deploy", () => {
    const preCountPage = page();
    delete preCountPage.category_counts;
    delete preCountPage.category_counts_exact;

    expect(
      validatePropertyCatalogPage(preCountPage).__propertyCatalogCursorStopped,
    ).toBeUndefined();

    const partialCountPage = { ...preCountPage, category_counts_exact: true };
    expect(
      validatePropertyCatalogPage(partialCountPage)
        .__propertyCatalogCursorStopped,
    ).toBe("malformed_page");
  });

  it("suppresses every definition when an activation changes mid-chain", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockResolvedValueOnce({
        data: {
          result: page({
            activation_fingerprint: "b".repeat(64),
            metrics: [
              {
                name: "customer.tier",
                property_id: "custom_attribute:customer.tier",
                property_kind: "custom_attribute",
                category: "custom_attribute",
              },
            ],
          }),
        },
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => usePropertyCatalog(), {
      wrapper: createQueryWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() =>
      expect(result.current.cursorStopReason).toBe("activation_mismatch"),
    );

    expect(result.current.metrics).toEqual([]);
    expect(result.current.hasNextPage).toBe(false);
    expect(result.current.queryReadState).toBe("degraded");
  });

  it("suppresses a cursor chain when exact category counts change", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockResolvedValueOnce({
        data: {
          result: page({
            category_counts: {
              all: 2,
              system_metric: 0,
              eval_metric: 0,
              annotation_metric: 0,
              custom_attribute: 2,
              custom_column: 0,
            },
          }),
        },
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => usePropertyCatalog(), {
      wrapper: createQueryWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() =>
      expect(result.current.cursorStopReason).toBe("category_count_mismatch"),
    );

    expect(result.current.metrics).toEqual([]);
    expect(result.current.categoryCounts).toBeNull();
    expect(result.current.categoryCountsExact).toBe(false);
  });

  it("suppresses every definition when one property id conflicts", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockResolvedValueOnce({
        data: {
          result: page({
            metrics: [
              {
                name: "customer.plan",
                display_name: "Conflicting label",
                property_id: "custom_attribute:customer.plan",
                property_kind: "custom_attribute",
                category: "custom_attribute",
              },
            ],
          }),
        },
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => usePropertyCatalog(), {
      wrapper: createQueryWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() =>
      expect(result.current.cursorStopReason).toBe("definition_conflict"),
    );

    expect(result.current.metrics).toEqual([]);
    expect(result.current.queryReadState).toBe("degraded");
  });

  it("suppresses every definition when one property id repeats", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: { result: page({ has_more: true, next_cursor: "cursor-2" }) },
      })
      .mockResolvedValueOnce({ data: { result: page() } });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(() => usePropertyCatalog(), {
      wrapper: createQueryWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    await act(() => result.current.fetchNextPage());
    await waitFor(() =>
      expect(result.current.cursorStopReason).toBe("duplicate_property"),
    );

    expect(result.current.metrics).toEqual([]);
    expect(result.current.queryReadState).toBe("degraded");
  });

  it.each([
    [
      "flattened application error",
      { statusCode: 503, code: "property_catalog_not_ready" },
    ],
    [
      "raw Axios error",
      {
        response: {
          status: 503,
          data: { code: "property_catalog_not_ready" },
        },
      },
    ],
  ])(
    "opens legacy fallback for a typed not-ready %s",
    async (_label, error) => {
      mocks.get.mockRejectedValueOnce(error);
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const { result } = renderHook(
        () =>
          usePropertyCatalog({
            allowLegacyNotReadyFallback: true,
            fallbackScopeKey: "workspace-a",
          }),
        { wrapper: createQueryWrapper(queryClient) },
      );

      await waitFor(() =>
        expect(result.current.legacyFallbackRequired).toBe(true),
      );
      expect(mocks.get).toHaveBeenCalledTimes(1);
    },
  );

  it("does not fall back for an unready catalog without the rollout code", async () => {
    mocks.get.mockRejectedValueOnce({
      response: {
        status: 503,
        data: { code: "service_unavailable" },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () =>
        usePropertyCatalog({
          allowLegacyNotReadyFallback: true,
          fallbackScopeKey: "workspace-a",
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.legacyFallbackRequired).toBe(false);
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

  it("uses one namespaced registry identity for every native value adapter", () => {
    expect(
      buildPropertyRegistryId({
        metricName: "model",
        metricType: "system_metric",
        source: "traces",
      }),
    ).toBe("system_attribute:traces:model");
    expect(
      buildPropertyRegistryId({
        metricName: "model",
        metricType: "custom_attribute",
        source: "traces",
      }),
    ).toBe("custom_attribute:model");
    expect(
      buildPropertyRegistryId({
        metricName: "eval-id",
        metricType: "eval_metric",
      }),
    ).toBe("eval:eval-id");
    expect(
      buildPropertyRegistryId({
        metricName: "label-id",
        metricType: "annotation_metric",
      }),
    ).toBe("annotation:label-id");
    expect(
      buildPropertyRegistryId({
        metricName: "column-id",
        source: "dataset_column",
      }),
    ).toBe("dataset_column:column-id");
  });

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
          property_id: "custom_attribute:final_status",
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

  it("publishes each non-empty system page without draining later checkpoints", async () => {
    const options = (start, count) =>
      Array.from({ length: count }, (_, index) => {
        const value = `model-${start + index}`;
        return { value, label: value };
      });
    const pages = [
      {
        values: options(1, 1),
        has_more: true,
        next_cursor: "initial-physical-2",
      },
      {
        values: options(2, 9),
        has_more: true,
        next_cursor: "second-visible-page",
      },
      {
        values: options(11, 2),
        has_more: true,
        next_cursor: "second-physical-2",
      },
      {
        values: options(13, 8),
        browse_status: "exhausted",
        has_more: false,
        next_cursor: null,
      },
    ];
    for (const page of pages) {
      mocks.get.mockResolvedValueOnce({
        data: {
          result: {
            ...page,
            query_complete: true,
            query_status: "complete",
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
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual(options(1, 1));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(result.current.data).toEqual(options(1, 10));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(3));
    expect(result.current.data).toEqual(options(1, 12));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(4));

    expect(result.current.data).toEqual(options(1, 20));
    expect(result.current.hasNextPage).toBe(false);
    expect(
      mocks.get.mock.calls.map(([, config]) => config.params.cursor || null),
    ).toEqual([
      null,
      "initial-physical-2",
      "second-visible-page",
      "second-physical-2",
    ]);
  });

  it("keeps a resumable cursor when a system page exceeds its bounded fill walk", async () => {
    for (let index = 1; index <= 13; index += 1) {
      mocks.get.mockResolvedValueOnce({
        data: {
          result: {
            values: [],
            query_complete: true,
            query_status: "complete",
            browse_status: "continuation",
            has_more: true,
            next_cursor: `checkpoint-${index}`,
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
    expect(mocks.get).toHaveBeenCalledTimes(13);
    expect(result.current.data).toEqual([]);
    expect(result.current.hasNextPage).toBe(true);
    expect(mocks.get.mock.calls[12][1].params.cursor).toBe("checkpoint-12");
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

describe("useDatasetColumnValues exact failure semantics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sends the stable dataset-column identity and normalizes exact options", async () => {
    mocks.get.mockResolvedValue({
      data: {
        result: {
          values: [
            { value: "alpha", label: "Alpha" },
            { value: "beta", label: "Beta" },
          ],
          query_complete: true,
          has_more: false,
          next_cursor: null,
        },
      },
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () =>
        useDatasetColumnValues({
          datasetId: "dataset-1",
          columnId: "column-1",
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(["alpha", "beta"]);
    expect(mocks.get).toHaveBeenCalledWith("/tracer/dashboard/filter_values/", {
      timeout: FILTER_VALUE_REQUEST_TIMEOUT_MS,
      params: {
        property_id: "dataset_column:column-1",
        metric_name: "column-1",
        metric_type: "custom_column",
        source: "dataset_column",
        dataset_id: "dataset-1",
        page_size: 50,
        project_ids: "",
      },
      signal: expect.any(AbortSignal),
    });
  });

  it("keeps the signed dataset vocabulary continuation available for Load more", async () => {
    mocks.get
      .mockResolvedValueOnce({
        data: {
          result: {
            values: ["alpha"],
            has_more: true,
            next_cursor: "dataset-cursor-2",
          },
        },
      })
      .mockResolvedValueOnce({
        data: {
          result: {
            values: ["beta"],
            has_more: false,
            next_cursor: null,
          },
        },
      });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () =>
        useDatasetColumnValues({
          datasetId: "dataset-1",
          columnId: "column-1",
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.hasNextPage).toBe(true));
    expect(result.current.data).toEqual(["alpha"]);
    await act(async () => result.current.fetchNextPage());
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
    expect(result.current.data).toEqual(["alpha", "beta"]);
    expect(mocks.get.mock.calls[1][1].params.cursor).toBe("dataset-cursor-2");
    expect(mocks.get.mock.calls[1][1].params.page_size).toBe(50);
  });

  it("does not relabel a failed exact read as an empty vocabulary", async () => {
    mocks.get.mockRejectedValue(new Error("temporarily unavailable"));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result } = renderHook(
      () =>
        useDatasetColumnValues({
          datasetId: "dataset-1",
          columnId: "column-1",
        }),
      { wrapper: createQueryWrapper(queryClient) },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
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
      {},
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
