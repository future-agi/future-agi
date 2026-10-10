import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "src/utils/axios";
import {
  classifySavedViewError,
  resolveExpectedRevision,
  mergeSavedViews,
  useRefreshSavedViews,
  useGetSavedViews,
  buildCreateSavedViewPayload,
  buildUpdateSavedViewPayload,
  findOwnDefaultView,
  getOwnViewNames,
  SAVED_VIEWS_KEY,
  tabTypeForSelectedTab,
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
      detail: (id) => `/tracer/saved-views/${id}/`,
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
    vi.resetAllMocks();
    axios.get.mockResolvedValue({ data: { result: { id: "view-1", revision: 1 } } });
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

  it("getOwnViewNames scopes to the current user's own views", () => {
    const views = [
      { name: "Mine", created_by: { id: "u1" } },
      { name: "Theirs", created_by: { id: "u2" } },
      { name: "Orphan" },
    ];
    expect(getOwnViewNames(views, "u1")).toEqual(["Mine"]);
    // Unknown user id → block nothing (server arbitrates), so no over-blocking
    // of other users' shared names during auth resolution.
    expect(getOwnViewNames(views, undefined)).toEqual([]);
    expect(getOwnViewNames(undefined, "u1")).toEqual([]);
  });

  it("tabTypeForSelectedTab maps UI tabs to backend tab_type", () => {
    expect(tabTypeForSelectedTab("trace")).toBe("traces");
    expect(tabTypeForSelectedTab("spans")).toBe("spans");
    expect(tabTypeForSelectedTab(undefined)).toBe("traces");
  });

  it("findOwnDefaultView adopts only the user's own default for the tab_type", () => {
    const views = [
      { id: "a", name: "Default View", tab_type: "traces", created_by: { id: "u1" } },
      { id: "b", name: "Default View", tab_type: "spans", created_by: { id: "u1" } },
      { id: "c", name: "Default View", tab_type: "traces", created_by: { id: "u2" } },
    ];
    // own traces default → adopted (drives update-by-id, not create)
    expect(findOwnDefaultView(views, { tabType: "traces", userId: "u1" })?.id).toBe("a");
    // scoped by tab_type — a spans click never grabs the traces default
    expect(findOwnDefaultView(views, { tabType: "spans", userId: "u1" })?.id).toBe("b");
    // a teammate's shared traces default is never adopted
    expect(findOwnDefaultView(views, { tabType: "traces", userId: "u3" })).toBeNull();
    // unknown user → create path
    expect(findOwnDefaultView(views, { tabType: "traces", userId: undefined })).toBeNull();
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
    vi.resetAllMocks();
    axios.get.mockResolvedValue({ data: { result: { id: "view-1", revision: 1 } } });
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

  it("propagates a duplicate-name 400 and does not append to the cache", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], {
      default_tabs: [],
      custom_views: [{ id: "view-1", name: "Errors" }],
    });
    axios.post.mockRejectedValueOnce({
      response: {
        status: 400,
        data: { message: "A view named 'Errors' already exists." },
      },
    });

    const { result } = renderHook(() => useCreateSavedView("project-1"), {
      wrapper: createQueryWrapper(queryClient),
    });

    await expect(
      result.current.mutateAsync({
        project_id: "project-1",
        name: "Errors",
        tab_type: "traces",
        config: {},
      }),
    ).rejects.toMatchObject({ response: { status: 400 } });

    // Cache is untouched — no duplicate row was optimistically added.
    expect(
      queryClient.getQueryData([SAVED_VIEWS_KEY, "project-1"]).custom_views,
    ).toEqual([{ id: "view-1", name: "Errors" }]);
  });

  it("updates a project saved view without sending tab_type", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], {
      default_tabs: [],
      custom_views: [{ id: "view-1", name: "Old", revision: 3 }],
    });
    axios.put.mockResolvedValueOnce({
      data: { result: { id: "view-1", name: "Renamed", revision: 4 } },
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
        expected_revision: 3,
        config: { display: { viewMode: "list" } },
      },
      { params: { project_id: "project-1" } },
    );
    await waitFor(() => {
      expect(
        queryClient.getQueryData([SAVED_VIEWS_KEY, "project-1"]).custom_views,
      ).toEqual([{ id: "view-1", name: "Renamed", revision: 4 }]);
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
      expected_revision: 1,
    }, { params: {} });
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
      params: { project_id: "project-1", expected_revision: 1 },
    });
  });

  it("reorders saved views and updates the selected query cache optimistically", async () => {
    const queryClient = createTestQueryClient();
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], {
      default_tabs: [],
      tab_order: { revision: 0, order: ["a", "b"] },
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
      expected_revision: 0,
      project_id: "project-1",
      order: [
        { id: "b", position: 0 },
        { id: "a", position: 1 },
      ],
    });
    expect(
      queryClient.getQueryData([SAVED_VIEWS_KEY, "project-1"]).custom_views,
    ).toEqual([
      { id: "b", name: "B", position: 1 },
      { id: "a", name: "A", position: 0 },
    ]);
  });
});

describe("TH-4798 conditional writes and consistency", () => {
  beforeEach(() => { vi.resetAllMocks(); });
  const key = [SAVED_VIEWS_KEY, "p"];
  const cached = (revision = 3) => ({ default_tabs: [], custom_views: [{ id: "v", revision, name: "Errors" }], tab_order: { revision: 2, order: ["v"] } });

  it.each([[409, "conflict"], [428, "precondition"], [403, "forbidden"], [404, "unavailable_record"], [503, "unavailable_transport"], [400, "validation"]])("classifies HTTP %s", (status, kind) => {
    expect(classifySavedViewError({ response: { status, data: { result: { current: { revision: 4 } } } } })).toMatchObject({ kind });
    expect(classifySavedViewError({ statusCode: status, result: { current: { revision: 4 } } })).toMatchObject({ kind });
  });
  it("classifies transport failure without claiming deletion", () => {
    expect(classifySavedViewError(new Error("Network Error"))).toEqual({ kind: "unavailable_transport" });
  });
  it.each(["hook", "other", "detail"])("resolves revision from %s", async (source) => {
    const client = createTestQueryClient();
    if (source !== "detail") client.setQueryData(source === "hook" ? key : [SAVED_VIEWS_KEY, "other"], cached());
    axios.get.mockResolvedValue({ data: { result: cached().custom_views[0] } });
    expect(await resolveExpectedRevision(client, key, "v", { project_id: "p" })).toBe(3);
    if (source === "detail") expect(axios.get).toHaveBeenCalledWith("/tracer/saved-views/v/", { params: { project_id: "p", consistency: "primary" } });
    else expect(axios.get).not.toHaveBeenCalled();
  });
  it("does not write when the primary detail is unavailable", async () => {
    axios.get.mockRejectedValue({ response: { status: 404 } });
    const { result } = renderHook(() => useUpdateSavedView("p"), { wrapper: createQueryWrapper() });
    await expect(result.current.mutateAsync({ id: "v", name: "Edit" })).rejects.toMatchObject({ response: { status: 404 } });
    expect(axios.put).not.toHaveBeenCalled();
  });
  it("retains revisions and order and sends the cached revision", async () => {
    const client = createTestQueryClient();
    axios.get.mockResolvedValue({ data: { result: cached() } });
    axios.put.mockResolvedValue({ data: { result: { id: "v", revision: 4, name: "Edit" } } });
    const { result } = renderHook(() => ({ list: useGetSavedViews("p"), update: useUpdateSavedView("p") }), { wrapper: createQueryWrapper(client) });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    expect(result.current.list.data.tab_order.revision).toBe(2);
    await result.current.update.mutateAsync({ id: "v", name: "Edit" });
    expect(axios.put).toHaveBeenCalledWith("/tracer/saved-views/v/", { name: "Edit", expected_revision: 3 }, { params: { project_id: "p" } });
    expect(client.getQueryData(key).custom_views[0].revision).toBe(4);
  });
  it("refresh marks a 30 second primary window", async () => {
    const client = createTestQueryClient();
    let now = 1000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    axios.get.mockResolvedValue({ data: { result: cached() } });
    const { result } = renderHook(() => ({ refresh: useRefreshSavedViews("p"), list: useGetSavedViews("p") }), { wrapper: createQueryWrapper(client) });
    await waitFor(() => expect(result.current.list.isSuccess).toBe(true));
    await result.current.refresh();
    expect(axios.get).toHaveBeenLastCalledWith("/tracer/saved-views/", expect.objectContaining({ params: { project_id: "p", consistency: "primary" } }));
    now += 31000;
    await result.current.list.refetch();
    expect(axios.get).toHaveBeenLastCalledWith("/tracer/saved-views/", expect.objectContaining({ params: { project_id: "p" } }));
    vi.restoreAllMocks();
  });
  it("never downgrades a revision and only primary reads can prune recent writes", () => {
    const current = cached(4);
    expect(mergeSavedViews(current, cached(3)).custom_views[0].revision).toBe(4);
    expect(mergeSavedViews(current, cached(5), { primary: true }).custom_views[0].revision).toBe(5);
    expect(mergeSavedViews(current, { custom_views: [] }, { writtenIds: new Set(["v"]) }).custom_views).toHaveLength(1);
    expect(mergeSavedViews(current, { custom_views: [] }, { primary: true }).custom_views).toHaveLength(0);
  });
  it("reorder rolls back and adopts the current order on conflict", async () => {
    const client = createTestQueryClient();
    client.setQueryData(key, { custom_views: [{ id: "a" }, { id: "b" }], tab_order: { revision: 2, order: ["a", "b"] } });
    axios.post.mockRejectedValue({ response: { status: 409, data: { result: { current: { revision: 3, order: ["a", "b"] } } } } });
    const { result } = renderHook(() => useReorderSavedViews("p"), { wrapper: createQueryWrapper(client) });
    await expect(result.current.mutateAsync({ project_id: "p", order: [{ id: "b", position: 0 }, { id: "a", position: 1 }] })).rejects.toBeTruthy();
    expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/reorder/", expect.objectContaining({ expected_revision: 2 }));
    expect(client.getQueryData(key).tab_order).toEqual({ revision: 3, order: ["a", "b"] });
    expect(client.getQueryData(key).custom_views.map((v) => v.id)).toEqual(["a", "b"]);
  });
  it("a late replica response cannot erase a newly acknowledged create", async () => {
    const client = createTestQueryClient();
    let resolveList;
    axios.get.mockImplementationOnce(() => new Promise((resolve) => { resolveList = resolve; }));
    axios.post.mockResolvedValue({ data: { result: { id: "v", revision: 1, name: "New" } } });
    const { result } = renderHook(() => ({ list: useGetSavedViews("p"), create: useCreateSavedView("p") }), { wrapper: createQueryWrapper(client) });
    await waitFor(() => expect(resolveList).toBeTypeOf("function"));
    await result.current.create.mutateAsync({ project_id: "p", name: "New", tab_type: "traces" });
    resolveList({ data: { result: { custom_views: [], tab_order: { revision: 0, order: [] } } } });
    await waitFor(() => expect(result.current.list.data.custom_views).toHaveLength(1));
  });
  it("reconciles an identical create retry after an uncertain commit", async () => {
    const client = createTestQueryClient();
    const payload = { project_id: "p", name: "Errors", tab_type: "traces", config: {} };
    axios.post.mockRejectedValueOnce(new Error("Network Error")).mockRejectedValueOnce({ response: { status: 400, data: { message: "already exists" } } });
    axios.get.mockResolvedValue({ data: { result: { custom_views: [{ ...payload, id: "v", revision: 1, is_owner: true }] } } });
    const { result } = renderHook(() => useCreateSavedView("p"), { wrapper: createQueryWrapper(client) });
    await expect(result.current.mutateAsync(payload)).rejects.toBeTruthy();
    const response = await result.current.mutateAsync(payload);
    expect(response.data.result.id).toBe("v");
    expect(axios.put).not.toHaveBeenCalled();
    expect(axios.get).toHaveBeenCalledWith("/tracer/saved-views/", expect.objectContaining({ params: { project_id: "p", consistency: "primary" } }));
  });
});
