import React from "react";
import PropTypes from "prop-types";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios from "src/utils/axios";
import {
  buildCreateSavedViewPayload,
  buildUpdateSavedViewPayload,
  defaultViewNameFor,
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
  useSetProjectDefaultView,
  useUpdateSavedView,
  useUpdateWorkspaceSavedView,
} from "../saved-views";

const auth = vi.hoisted(() => ({ user: { id: "u1" }, authenticated: true }));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => auth,
}));
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

  it("defaultViewNameFor gives each tab type a default name of its own", () => {
    // A user's project view names are unique across tab types, so a shared
    // name made the second tab type's default fail with "already exists".
    expect(defaultViewNameFor("traces")).toBe("Default View");
    expect(defaultViewNameFor("spans")).toBe("Default spans view");
    expect(defaultViewNameFor("sessions")).toBe("Default sessions view");
    expect(defaultViewNameFor("users")).toBe("Default users view");
    expect(defaultViewNameFor(undefined)).toBe("Default View");
  });

  it("findOwnDefaultView adopts only the user's own default for the tab_type", () => {
    // One user can't hold two views of one name in a project, so each tab
    // type's default carries its own name.
    const views = [
      {
        id: "a",
        name: "Default View",
        tab_type: "traces",
        created_by: { id: "u1" },
      },
      {
        id: "b",
        name: "Default spans view",
        tab_type: "spans",
        created_by: { id: "u1" },
      },
      {
        id: "c",
        name: "Default View",
        tab_type: "traces",
        created_by: { id: "u2" },
      },
    ];
    // own traces default → adopted (drives update-by-id, not create)
    expect(
      findOwnDefaultView(views, { tabType: "traces", userId: "u1" })?.id,
    ).toBe("a");
    // scoped by tab_type — a spans click never grabs the traces default
    expect(
      findOwnDefaultView(views, { tabType: "spans", userId: "u1" })?.id,
    ).toBe("b");
    // a teammate's shared traces default is never adopted
    expect(
      findOwnDefaultView(views, { tabType: "traces", userId: "u3" }),
    ).toBeNull();
    // unknown user → create path
    expect(
      findOwnDefaultView(views, { tabType: "traces", userId: undefined }),
    ).toBeNull();
  });

  it("findOwnDefaultView still adopts a spans default saved as Default View", () => {
    const view = (id, name, tabType) => ({
      id,
      name,
      tab_type: tabType,
      created_by: { id: "u1" },
    });
    const views = [
      view("s", "Default View", "spans"),
      view("x", "Default View", "sessions"),
    ];
    const find = (tabType, list = views) =>
      findOwnDefaultView(list, { tabType, userId: "u1" });
    // Saved before spans had a name of its own: still the user's default.
    expect(find("spans")?.id).toBe("s");
    // Only spans keeps the legacy name; a Sessions view of that name is not
    // its default, while its own name is.
    expect(find("sessions")).toBeNull();
    const sessionsDefault = view("y", "Default sessions view", "sessions");
    expect(find("sessions", [sessionsDefault])?.id).toBe("y");
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

// "Share with project": the open saved view is shared, else the user's own
// default for the tab type is updated, else a new one is created. The toast
// names the shared view and never calls it a default for everyone.
describe("useSetProjectDefaultView", () => {
  const config = { display: { cellHeight: "Large" } };
  const own = (overrides) => ({
    id: "own-sessions",
    name: "Default sessions view",
    tab_type: "sessions",
    created_by: { id: "u1" },
    ...overrides,
  });

  // Lets a mutation that was started reach its request (react-query calls
  // the request a few promise ticks after mutate).
  const settle = () => act(() => new Promise((done) => setTimeout(done, 0)));

  const renderSetDefault = (customViews) => {
    const queryClient = createTestQueryClient();
    const views = { default_tabs: [], custom_views: customViews };
    queryClient.setQueryData([SAVED_VIEWS_KEY, "project-1"], views);
    axios.get.mockResolvedValue({ data: { result: views } });
    return renderHook(() => useSetProjectDefaultView("project-1"), {
      wrapper: createQueryWrapper(queryClient),
    });
  };

  beforeEach(() => {
    vi.clearAllMocks();
    auth.user = { id: "u1" };
  });

  it("creates the tab type's project default when the user has none", async () => {
    axios.post.mockResolvedValueOnce({
      data: { result: { id: "new-1", name: "Default sessions view" } },
    });
    // A teammate's shared default is never adopted.
    const { result } = renderSetDefault([
      own({ id: "theirs", created_by: { id: "u2" } }),
      own({ id: "own-traces", name: "Default View", tab_type: "traces" }),
    ]);
    const onSaved = vi.fn();

    act(() =>
      result.current({
        tabType: "sessions",
        config,
        activeViewId: null,
        onSaved,
      }),
    );

    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith(
        { id: "new-1", name: "Default sessions view" },
        { created: true },
      ),
    );
    expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/", {
      project_id: "project-1",
      name: "Default sessions view",
      tab_type: "sessions",
      visibility: "project",
      config,
    });
    expect(axios.put).not.toHaveBeenCalled();
    // The Sessions success toast.
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Shared with the project as Default sessions view",
      { variant: "success" },
    );
  });

  // Bug fix: traces and spans both created "Default View", and a user's
  // project view names are unique across tab types, so the second one failed
  // with "already exists".
  it("a spans default doesn't take the traces default's name", async () => {
    expect(defaultViewNameFor("spans")).not.toBe(defaultViewNameFor("traces"));
    axios.post.mockResolvedValueOnce({
      data: { result: { id: "new-spans", name: "Default spans view" } },
    });
    const { result } = renderSetDefault([
      own({ id: "own-traces", name: "Default View", tab_type: "traces" }),
    ]);

    act(() => result.current({ tabType: "spans", config, activeViewId: null }));

    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/tracer/saved-views/", {
        project_id: "project-1",
        name: "Default spans view",
        tab_type: "spans",
        visibility: "project",
        config,
      }),
    );
    expect(axios.put).not.toHaveBeenCalled();
  });

  it("updates the user's own default for the tab type instead of creating one", async () => {
    axios.put.mockResolvedValueOnce({
      data: {
        result: {
          id: "own-sessions",
          name: "Default sessions view",
          visibility: "project",
        },
      },
    });
    const { result } = renderSetDefault([
      own({ id: "own-traces", name: "Default View", tab_type: "traces" }),
      own(),
    ]);
    const onSaved = vi.fn();

    act(() =>
      result.current({
        tabType: "sessions",
        config,
        activeViewId: null,
        onSaved,
      }),
    );

    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith(
        expect.objectContaining({ id: "own-sessions" }),
        { created: false },
      ),
    );
    expect(axios.put).toHaveBeenCalledWith(
      "/tracer/saved-views/own-sessions/",
      { visibility: "project", config },
      { params: { project_id: "project-1" } },
    );
    expect(axios.post).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Shared with the project as Default sessions view",
      { variant: "success" },
    );
  });

  it("shares the open saved view with the project", async () => {
    axios.put.mockResolvedValueOnce({
      data: {
        result: { id: "open-view", name: "Errors", visibility: "project" },
      },
    });
    const { result } = renderSetDefault([own()]);
    const onSaved = vi.fn();

    act(() =>
      result.current({
        tabType: "sessions",
        config,
        activeViewId: "open-view",
        onSaved,
      }),
    );

    await waitFor(() =>
      expect(onSaved).toHaveBeenCalledWith(
        expect.objectContaining({ id: "open-view" }),
        { created: false },
      ),
    );
    expect(axios.put).toHaveBeenCalledWith(
      "/tracer/saved-views/open-view/",
      { visibility: "project", config },
      { params: { project_id: "project-1" } },
    );
    expect(axios.post).not.toHaveBeenCalled();
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      "Shared with the project as Errors",
      { variant: "success" },
    );
  });

  it("says why when the save fails and reports no success", async () => {
    axios.post.mockRejectedValueOnce({
      response: {
        status: 400,
        data: {
          message: "A view named 'Default sessions view' already exists.",
        },
      },
    });
    const { result } = renderSetDefault([]);
    const onSaved = vi.fn();

    act(() =>
      result.current({
        tabType: "sessions",
        config,
        activeViewId: null,
        onSaved,
      }),
    );

    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), {
        variant: "error",
      }),
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(expect.any(String), {
      variant: "success",
    });
  });

  // A legacy spans default still named "Default View" blocked the person's
  // Traces default ("already exists": names are unique per person across tab
  // types). Adopting it renames it, which frees "Default View".
  it("renames a legacy spans default so the Traces default can take its name", async () => {
    axios.put.mockResolvedValueOnce({
      data: { result: { id: "legacy-spans", name: "Default spans view" } },
    });
    const { result } = renderSetDefault([
      own({ id: "legacy-spans", name: "Default View", tab_type: "spans" }),
    ]);

    act(() => result.current({ tabType: "spans", config, activeViewId: null }));

    await waitFor(() =>
      expect(axios.put).toHaveBeenCalledWith(
        "/tracer/saved-views/legacy-spans/",
        { name: "Default spans view", visibility: "project", config },
        { params: { project_id: "project-1" } },
      ),
    );
    expect(axios.post).not.toHaveBeenCalled();
  });

  // A second click while the first request is in flight would create again
  // and toast "already exists" after the success.
  it("ignores a second click while a share is in flight", async () => {
    let finish;
    axios.post.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { result } = renderSetDefault([]);
    const share = () =>
      result.current({ tabType: "sessions", config, activeViewId: null });

    act(() => share());
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    act(() => share());
    await settle();
    expect(axios.post).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish({
        data: { result: { id: "new-1", name: "Default sessions view" } },
      });
    });
    await waitFor(() =>
      expect(enqueueSnackbar).toHaveBeenCalledWith(
        "Shared with the project as Default sessions view",
        { variant: "success" },
      ),
    );
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  // Create vs update depends on the person's own views: until the list and
  // the user have loaded, a click on a fixed tab is ignored, so an existing
  // default is never created again.
  it("waits for the saved views and the user before choosing create or update", async () => {
    axios.get.mockReturnValueOnce(new Promise(() => {}));
    const unloaded = renderHook(() => useSetProjectDefaultView("project-1"), {
      wrapper: createQueryWrapper(),
    });
    act(() =>
      unloaded.result.current({
        tabType: "sessions",
        config,
        activeViewId: null,
      }),
    );
    await settle();
    expect(axios.post).not.toHaveBeenCalled();
    expect(axios.put).not.toHaveBeenCalled();

    auth.user = null;
    const { result, rerender } = renderSetDefault([own()]);
    act(() =>
      result.current({ tabType: "sessions", config, activeViewId: null }),
    );
    await settle();
    expect(axios.post).not.toHaveBeenCalled();
    expect(axios.put).not.toHaveBeenCalled();

    auth.user = { id: "u1" };
    rerender();
    act(() =>
      result.current({ tabType: "sessions", config, activeViewId: null }),
    );
    await waitFor(() => expect(axios.put).toHaveBeenCalledTimes(1));
    expect(axios.post).not.toHaveBeenCalled();
  });
});
