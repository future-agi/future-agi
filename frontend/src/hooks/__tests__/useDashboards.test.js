import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
      detail: (id) => `/tracer/dashboard/${id}/`,
      resolveWorkspace: (id) => `/tracer/dashboard/${id}/resolve-workspace/`,
      widgets: (dashboardId) => `/tracer/dashboard/${dashboardId}/widgets/`,
      widgetDetail: (dashboardId, widgetId) =>
        `/tracer/dashboard/${dashboardId}/widgets/${widgetId}/`,
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
  useResolveDashboardWorkspace,
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

describe("useResolveDashboardWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not auto-fetch (enabled: false) and returns null data by default", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const { result } = renderHook(
      () => useResolveDashboardWorkspace("dash-1"),
      { wrapper: createQueryWrapper(queryClient) },
    );

    expect(result.current.data).toBeUndefined();
    expect(result.current.isFetching).toBe(false);
  });

  it("resolves workspace when refetch is called", async () => {
    mocks.get.mockResolvedValueOnce({
      data: { result: { workspace_id: "ws-1", workspace_name: "Test WS" } },
    });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const { result } = renderHook(
      () => useResolveDashboardWorkspace("dash-1"),
      { wrapper: createQueryWrapper(queryClient) },
    );

    result.current.refetch();

    await waitFor(() => expect(result.current.isFetching).toBe(false));

    expect(mocks.get).toHaveBeenCalledWith(
      "/tracer/dashboard/dash-1/resolve-workspace/",
    );
    expect(result.current.data).toEqual({
      workspace_id: "ws-1",
      workspace_name: "Test WS",
    });
  });
});
