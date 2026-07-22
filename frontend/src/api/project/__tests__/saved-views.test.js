import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  buildCreateSavedViewPayload,
  buildUpdateSavedViewPayload,
  SAVED_VIEWS_KEY,
  serializeSavedViewConfig,
  useCreateSavedView,
  useCreateWorkspaceSavedView,
  useDeleteSavedView,
  useDuplicateSavedView,
  useReorderSavedViews,
  useUpdateSavedView,
  useUpdateWorkspaceSavedView,
} from "../saved-views";

vi.mock("src/utils/axios", () => ({
  default: {
    delete: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
  endpoints: {
    savedViews: {
      list: "/tracer/saved-views/",
      create: "/tracer/saved-views/",
      update: (id) => `/tracer/saved-views/${id}/`,
      delete: (id) => `/tracer/saved-views/${id}/`,
      duplicate: (id) => `/tracer/saved-views/${id}/duplicate/`,
      reorder: "/tracer/saved-views/reorder/",
    },
  },
}));

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function createQueryWrapper(queryClient = createTestQueryClient()) {
  function QueryWrapper({ children }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children,
    );
  }

  QueryWrapper.propTypes = {
    children: PropTypes.node,
  };

  return QueryWrapper;
}

const canonicalFilter = {
  id: "ui-row-1",
  _meta: { source: "panel" },
  column_id: "status",
  display_name: "Status",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
};

describe("saved view payload contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("serializes saved-view filters to the backend canonical shape", () => {
    expect(
      serializeSavedViewConfig({
        display: { density: "compact" },
        filters: [canonicalFilter],
      }),
    ).toEqual({
      display: { density: "compact" },
      filters: [
        {
          column_id: "status",
          display_name: "Status",
          filter_config: {
            col_type: "SYSTEM_METRIC",
            filter_type: "text",
            filter_op: "equals",
            filter_value: "ERROR",
          },
        },
      ],
    });
  });

  it("rejects non-contract saved-view config keys before the API call", () => {
    expect(() =>
      serializeSavedViewConfig({
        extraFilters: [canonicalFilter],
      }),
    ).toThrow("Unknown saved view config keys: extraFilters");
  });

  it("rejects object filters because the backend contract uses filter lists", () => {
    expect(() =>
      serializeSavedViewConfig({
        filters: { extraFilters: [canonicalFilter] },
      }),
    ).toThrow('Saved view config "filters" must be a filter list.');
  });

  it("keeps create payloads to create fields and canonical config", () => {
    expect(
      buildCreateSavedViewPayload({
        id: "ignored",
        project_id: "project-1",
        name: "Errors",
        tab_type: "traces",
        visibility: "personal",
        config: { filters: [canonicalFilter] },
      }),
    ).toEqual({
      project_id: "project-1",
      name: "Errors",
      tab_type: "traces",
      visibility: "personal",
      config: {
        filters: [
          {
            column_id: "status",
            display_name: "Status",
            filter_config: {
              col_type: "SYSTEM_METRIC",
              filter_type: "text",
              filter_op: "equals",
              filter_value: "ERROR",
            },
          },
        ],
      },
    });
  });

  it("strips create-only fields from update payloads", () => {
    expect(
      buildUpdateSavedViewPayload({
        id: "view-1",
        project_id: "project-1",
        tab_type: "traces",
        name: "Errors",
        visibility: "project",
        config: { display: { viewMode: "grid" } },
      }),
    ).toEqual({
      name: "Errors",
      visibility: "project",
      config: { display: { viewMode: "grid" } },
    });
  });
});

describe("saved view API actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("creates a project saved view and updates the selected query cache", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], {
      default_tabs: [],
      custom_views: [],
    });
    axios.post.mockResolvedValueOnce({
      data: { result: { id: "view-1", name: "Errors" } },
    });

    const { result } = renderHook(() => useCreateSavedView("project-1"), {
      wrapper: createQueryWrapper(queryClient),
    });

    await result.current.mutateAsync({
      project_id: "project-1",
      name: "Errors",
      tab_type: "traces",
      config: { filters: [canonicalFilter] },
    });

    expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/", {
      project_id: "project-1",
      name: "Errors",
      tab_type: "traces",
      config: {
        filters: [
          expect.objectContaining({
            column_id: "status",
            filter_config: expect.objectContaining({
              filter_type: "text",
              filter_op: "equals",
            }),
          }),
        ],
      },
    });
    await waitFor(() => {
      expect(
        queryClient.getQueryData([SAVED_VIEWS_KEY, "project-1"]).custom_views,
      ).toEqual([{ id: "view-1", name: "Errors" }]);
    });
  });

  it("updates a project saved view without sending tab_type", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], {
      default_tabs: [],
      custom_views: [{ id: "view-1", name: "Old" }],
    });
    axios.put.mockResolvedValueOnce({
      data: { result: { id: "view-1", name: "Renamed" } },
    });

    const { result } = renderHook(() => useUpdateSavedView("project-1"), {
      wrapper: createQueryWrapper(queryClient),
    });

    await result.current.mutateAsync({
      id: "view-1",
      project_id: "project-1",
      tab_type: "traces",
      name: "Renamed",
      config: { display: { viewMode: "list" } },
    });

    expect(axios.put).toHaveBeenCalledWith(
      "/tracer/saved-views/view-1/",
      {
        name: "Renamed",
        config: { display: { viewMode: "list" } },
      },
      { params: { project_id: "project-1" } },
    );
    await waitFor(() => {
      expect(
        queryClient.getQueryData([SAVED_VIEWS_KEY, "project-1"]).custom_views,
      ).toEqual([{ id: "view-1", name: "Renamed" }]);
    });
  });

  it("creates workspace saved views with the workspace tab type", async () => {
    axios.post.mockResolvedValueOnce({
      data: { result: { id: "view-1", name: "Users" } },
    });
    const { result } = renderHook(() => useCreateWorkspaceSavedView("users"), {
      wrapper: createQueryWrapper(),
    });

    await result.current.mutateAsync({
      name: "Users",
      tab_type: "ignored",
      config: {},
    });

    expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/", {
      name: "Users",
      tab_type: "users",
      config: {},
    });
  });

  it("updates workspace saved views without sending tab_type", async () => {
    axios.put.mockResolvedValueOnce({
      data: { result: { id: "view-1", name: "Users" } },
    });
    const { result } = renderHook(() => useUpdateWorkspaceSavedView("users"), {
      wrapper: createQueryWrapper(),
    });

    await result.current.mutateAsync({
      id: "view-1",
      tab_type: "users",
      name: "Users",
      config: {},
    });

    expect(axios.put).toHaveBeenCalledWith("/tracer/saved-views/view-1/", {
      name: "Users",
      config: {},
    });
  });

  it("duplicates a saved view with project scoping", async () => {
    axios.post.mockResolvedValueOnce({
      data: { result: { id: "copy-1", name: "Copy" } },
    });
    const { result } = renderHook(() => useDuplicateSavedView("project-1"), {
      wrapper: createQueryWrapper(),
    });

    await result.current.mutateAsync({ id: "view-1", name: "Copy" });

    expect(axios.post).toHaveBeenCalledWith(
      "/tracer/saved-views/view-1/duplicate/",
      { name: "Copy" },
      { params: { project_id: "project-1" } },
    );
  });

  it("deletes a saved view with project scoping", async () => {
    axios.delete.mockResolvedValueOnce({ data: { result: {} } });
    const { result } = renderHook(() => useDeleteSavedView("project-1"), {
      wrapper: createQueryWrapper(),
    });

    await result.current.mutateAsync("view-1");

    expect(axios.delete).toHaveBeenCalledWith("/tracer/saved-views/view-1/", {
      params: { project_id: "project-1" },
    });
  });

  it("reorders saved views and updates the selected query cache optimistically", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], {
      default_tabs: [],
      custom_views: [
        { id: "a", name: "A", position: 0 },
        { id: "b", name: "B", position: 1 },
      ],
    });
    axios.post.mockResolvedValueOnce({ data: { result: { success: true } } });
    const { result } = renderHook(() => useReorderSavedViews("project-1"), {
      wrapper: createQueryWrapper(queryClient),
    });

    await result.current.mutateAsync({
      project_id: "project-1",
      order: [
        { id: "b", position: 0 },
        { id: "a", position: 1 },
      ],
    });

    expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/reorder/", {
      project_id: "project-1",
      order: [
        { id: "b", position: 0 },
        { id: "a", position: 1 },
      ],
    });
    expect(
      queryClient.getQueryData([SAVED_VIEWS_KEY, "project-1"]).custom_views,
    ).toEqual([
      { id: "b", name: "B", position: 0 },
      { id: "a", name: "A", position: 1 },
    ]);
  });
});
