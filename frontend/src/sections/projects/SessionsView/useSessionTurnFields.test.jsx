import React from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useSessionTurnFields from "./useSessionTurnFields";

const state = vi.hoisted(() => ({
  workspace: "w1",
  user: "u1",
  views: [],
  get: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: state.workspace }),
}));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ user: { id: state.user } }),
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args) },
  endpoints: { savedViews: { list: "/saved-views" } },
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useCreateSavedView: () => ({ mutateAsync: state.create }),
    useUpdateSavedView: () => ({ mutateAsync: state.update }),
  };
});

const selected = [{ kind: "value", label: "Answer", path: "output.answer" }];
const view = (overrides = {}) => ({
  id: "default",
  name: "Default sessions view",
  tab_type: "sessions",
  created_by: { id: "u1" },
  visibility: "personal",
  config: {
    display: { sessionTurnFields: selected, cellHeight: "Tall" },
    sort: { field: "start_time" },
  },
  ...overrides,
});
let client;
const wrapper = ({ children }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  state.workspace = "w1";
  state.user = "u1";
  state.views = [view()];
  state.get.mockReset().mockImplementation(async () => ({
    data: { result: { custom_views: state.views } },
  }));
  state.update.mockReset().mockImplementation(async (payload) => ({
    data: { result: { ...view(), ...payload } },
  }));
  state.create.mockReset().mockImplementation(async (payload) => ({
    data: { result: { ...view({ id: "new" }), ...payload } },
  }));
});
afterEach(() => {
  cleanup();
  client.clear();
});

describe("project-scoped server turn preferences", () => {
  it("hydrates a personal default without browser storage and persists an empty selection", async () => {
    const { result, unmount } = renderHook(() => useSessionTurnFields("p1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.fields).toEqual(selected);
    await act(async () => {
      await result.current.save([]);
    });
    expect(state.update).toHaveBeenCalledWith({
      id: "default",
      config: {
        display: { sessionTurnFields: [], cellHeight: "Tall" },
        sort: { field: "start_time" },
      },
    });
    state.views = [view({ config: state.update.mock.calls[0][0].config })];
    unmount();
    client.clear();
    const reopened = renderHook(() => useSessionTurnFields("p1"), { wrapper });
    await waitFor(() => expect(reopened.result.current.ready).toBe(true));
    expect(reopened.result.current.fields).toEqual([]);
  });

  it("uses an active saved view before the personal fallback and saves the current complete config", async () => {
    state.views.push(
      view({
        id: "active",
        name: "Investigation",
        config: {
          display: { sessionTurnFields: [], retained: true },
          columns: ["input"],
        },
      }),
    );
    const { result } = renderHook(
      () => useSessionTurnFields("p1", { activeViewId: "active" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.fields).toEqual([]);
    await act(async () => {
      await result.current.save(selected, {
        columns: ["input"],
        display: { retained: true, cellHeight: "Short" },
        extra_filters: [],
      });
    });
    expect(state.update).toHaveBeenCalledWith({
      id: "active",
      config: {
        columns: ["input"],
        extra_filters: [],
        display: {
          retained: true,
          cellHeight: "Short",
          sessionTurnFields: selected,
        },
      },
    });
  });

  it("does not restore the last custom column removed before saving turn fields", async () => {
    const storedConfig = {
      display: {
        cellHeight: "Tall",
        customColumns: [{ field: "output.answer", groupBy: "Custom Columns" }],
      },
      extra_filters: [],
    };
    state.views = [view({ id: "active", config: storedConfig })];
    const { result } = renderHook(
      () => useSessionTurnFields("p1", { activeViewId: "active" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.ready).toBe(true));
    // buildViewConfig omits customColumns after the final column is removed.
    const currentConfig = {
      display: { cellHeight: "Tall" },
      extra_filters: [],
    };
    const expectedConfig = {
      ...currentConfig,
      display: { ...currentConfig.display, sessionTurnFields: selected },
    };
    let saved;
    await act(async () => {
      saved = await result.current.save(selected, currentConfig);
    });
    expect(state.update).toHaveBeenCalledWith({
      id: "active",
      config: expectedConfig,
    });
    // SessionsView hydrates this returned config after a successful save.
    expect(saved).toEqual({ config: expectedConfig, savedActiveView: true });
    expect(saved.config.display).not.toHaveProperty("customColumns");
    expect(currentConfig.display).not.toHaveProperty("sessionTurnFields");
    expect(storedConfig.display.customColumns).toHaveLength(1);
  });

  it("does not restore comparison filters omitted from the current snapshot when comparison is off", async () => {
    state.views = [
      view({
        id: "active",
        config: {
          display: { showCompare: true },
          compare_date_filter: { days: 7 },
          compare_extra_filters: [{ field: "status", value: "error" }],
        },
      }),
    ];
    const { result } = renderHook(
      () => useSessionTurnFields("p1", { activeViewId: "active" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.ready).toBe(true));
    const currentConfig = {
      display: { showCompare: false },
      extra_filters: [],
    };
    const expectedConfig = {
      ...currentConfig,
      display: { ...currentConfig.display, sessionTurnFields: selected },
    };
    let saved;
    await act(async () => {
      saved = await result.current.save(selected, currentConfig);
    });
    expect(state.update).toHaveBeenCalledWith({
      id: "active",
      config: expectedConfig,
    });
    expect(saved).toEqual({ config: expectedConfig, savedActiveView: true });
    expect(saved.config).not.toHaveProperty("compare_date_filter");
    expect(saved.config).not.toHaveProperty("compare_extra_filters");
  });

  it("never updates another member's shared view or promotes personal visibility", async () => {
    state.views = [
      view({
        id: "shared",
        created_by: { id: "u2" },
        visibility: "project",
        config: { display: { sessionTurnFields: [] } },
      }),
    ];
    const { result } = renderHook(
      () => useSessionTurnFields("p1", { activeViewId: "shared" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.ready).toBe(true));
    await act(async () => {
      await result.current.save(selected);
    });
    expect(state.update).not.toHaveBeenCalled();
    expect(state.create).toHaveBeenCalledWith(
      expect.objectContaining({
        project_id: "p1",
        name: "Default sessions view",
        tab_type: "sessions",
        visibility: "personal",
      }),
    );
  });

  it("resets immediately on project, workspace and user changes and refuses writes before hydration", async () => {
    const { result, rerender } = renderHook(
      ({ project }) => useSessionTurnFields(project),
      { wrapper, initialProps: { project: "p1" } },
    );
    await waitFor(() => expect(result.current.fields).toEqual(selected));
    state.get.mockImplementation(() => new Promise(() => {}));
    rerender({ project: "p2" });
    expect(result.current.ready).toBe(false);
    expect(result.current.fields).not.toEqual(selected);
    await expect(result.current.save([])).rejects.toThrow();
    state.workspace = "w2";
    rerender({ project: "p1" });
    expect(result.current.ready).toBe(false);
    state.workspace = "w1";
    state.user = "u2";
    rerender({ project: "p1" });
    expect(result.current.ready).toBe(false);
    expect(state.update).not.toHaveBeenCalled();
    rerender({ project: null });
    expect(result.current.ready).toBe(false);
  });

  it("does not let an in-flight save replace the next project's fields", async () => {
    let finish;
    state.update.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result, rerender } = renderHook(
      ({ project }) => useSessionTurnFields(project),
      { wrapper, initialProps: { project: "p1" } },
    );
    await waitFor(() => expect(result.current.ready).toBe(true));
    let saving;
    act(() => {
      saving = result.current.save([]);
    });
    state.views = [
      view({
        config: {
          display: {
            sessionTurnFields: [{ kind: "metric", metric: "total_tokens" }],
          },
        },
      }),
    ];
    rerender({ project: "p2" });
    await waitFor(() => expect(result.current.ready).toBe(true));
    await act(async () => {
      finish({
        data: {
          result: view({ config: { display: { sessionTurnFields: [] } } }),
        },
      });
      await saving;
    });
    expect(result.current.fields).toEqual([
      { kind: "metric", metric: "total_tokens" },
    ]);
  });

  it("keeps server fields unchanged on save failure and permits retry", async () => {
    const { result } = renderHook(() => useSessionTurnFields("p1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.ready).toBe(true));
    state.update.mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      await expect(result.current.save([])).rejects.toThrow("offline");
    });
    expect(result.current.fields).toEqual(selected);
    await act(async () => {
      await result.current.save([]);
    });
    expect(result.current.fields).toEqual([]);
  });
});
