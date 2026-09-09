import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "src/utils/test-utils";

const state = vi.hoisted(() => ({
  options: null,
  header: null,
  get: vi.fn(),
  workspace: "workspace-1",
  realQuery: false,
  placeholder: false,
  history: null,
  scroll: null,
  fetchNext: vi.fn(),
}));
vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useInfiniteQuery: (options) => {
      state.options = options;
      if (state.realQuery) return actual.useInfiniteQuery(options);
      return {
        isPlaceholderData: state.placeholder,
        data: {
          pages: [
            {
              data: {
                result: {
                  response: [{ id: "cached-row" }],
                  session_metadata: {
                    next_session_id: "session-2",
                    previous_session_id: "session-0",
                  },
                },
              },
            },
          ],
        },
        fetchNextPage: state.fetchNext,
      };
    },
  };
});
vi.mock("src/utils/axios", () => ({
  readQuery: (...args) => state.get(...args),
  default: { get: (...args) => state.get(...args) },
  endpoints: { project: { traceSessionQuery: (id) => `/sessions/${id}/query/` } },
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: state.workspace }),
}));
vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: (callback) => {
    state.scroll = callback;
    return { current: null };
  },
}));
vi.mock("./Header", () => ({
  default: (props) => {
    state.header = props;
    return null;
  },
}));
vi.mock("./SessionHistory", () => ({
  default: (props) => {
    state.history = props;
    return (
      <div data-testid="session-history">
        {props.traceDetail.map((row) => row.id).join(",")}
      </div>
    );
  },
}));
vi.mock("./SessionEvalsList", () => ({ default: () => null }));
vi.mock("./Filters", () => ({ default: () => null }));
vi.mock("src/components/InlineAnnotator", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("./useTraceDrawerStore", () => ({
  useTraceDrawerStore: () => ({ viewType: "list", setViewType: vi.fn() }),
}));
vi.mock("src/utils/utils", () => ({ formatMs: (value) => value }));
vi.mock("src/utils/logger", () => ({ default: { debug: vi.fn() } }));
import TracesDrawer from "./TracesDrawer";
import {
  QueryClient,
  QueryClientProvider,
  keepPreviousData,
} from "@tanstack/react-query";

const context = {
  workspace_id: "workspace-1",
  project_id: null,
  user_id: "public-user",
  filters: [
    {
      column_id: "company_id",
      property_id: "custom_attribute:company_id",
      source: "traces",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "in",
        filter_value: [2, 5, 10],
        attribute_value_types: ["number", "number", "number"],
      },
    },
  ],
  sort_params: [{ column_id: "total_tokens", direction: "asc" }],
  cursor_mode: false,
};

describe("Session drawer navigation request", () => {
  beforeEach(() => {
    state.get.mockReset();
    state.workspace = "workspace-1";
    state.realQuery = false;
    state.placeholder = false;
    state.history = null;
    state.fetchNext.mockReset();
  });
  it.each(["project-1", null])(
    "loads the same-workspace %s session using the provider contract",
    async (projectId) => {
      state.realQuery = true;
      const client = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            gcTime: Infinity,
          },
        },
      });
      const navigationContext = { ...context, project_id: projectId };
      state.get.mockResolvedValue({
        data: {
          result: {
            response: [{ id: "same-workspace-row" }],
            session_metadata: { next_session_id: "session-2" },
          },
        },
      });
      const view = render(
        <QueryClientProvider client={client}>
          <TracesDrawer
            open
            onClose={vi.fn()}
            rowData={{ session_id: "session-1" }}
            navigationContext={navigationContext}
          />
        </QueryClientProvider>,
      );
      try {
        expect(state.options.enabled).toBe(true);
        expect(state.options.queryKey).toContain("workspace-1");
        await waitFor(() =>
          expect(screen.getByTestId("session-history")).toHaveTextContent(
            "same-workspace-row",
          ),
        );
        expect(state.get).toHaveBeenCalledTimes(1);
        expect(state.get).toHaveBeenCalledWith("/sessions/session-1/query/", {
          params: {
            page_number: 0,
            page_size: 10,
            navigation_context: JSON.stringify(navigationContext),
          },
        });
        expect(state.header.hasNextTrace).toBe(true);
      } finally {
        view.unmount();
        client.clear();
      }
    },
  );
  it("keeps the originating context across next/previous requests and cache keys", async () => {
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
        navigationContext={context}
      />,
    );
    expect(state.options.queryKey).toContainEqual(context);
    await state.options.queryFn({ pageParam: 0 });
    expect(
      JSON.parse(state.get.mock.calls[0][1].params.navigation_context),
    ).toEqual(context);
    await act(async () => state.header.handleNextTrace());
    await state.options.queryFn({ pageParam: 1 });
    expect(state.get.mock.calls[1][0]).toBe("/sessions/session-2/query/");
    expect(
      JSON.parse(state.get.mock.calls[1][1].params.navigation_context),
    ).toEqual(context);
    await act(async () => state.header.handlePrevTrace());
    expect(state.options.queryKey).toContain("session-0");
  });
  it("preserves the request contract without list context", async () => {
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
        userIdForUserMode="old-user"
      />,
    );
    await state.options.queryFn({ pageParam: 0 });
    expect(state.get.mock.calls[0][1].params).toEqual({
      page_number: 0,
      page_size: 10,
      user_id: "old-user",
    });
  });
  it("does not send a captured list into a different active workspace", () => {
    state.workspace = "workspace-2";
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
        navigationContext={context}
      />,
    );
    expect(state.options.enabled).toBe(false);
    expect(state.header.hasNextTrace).toBe(false);
    expect(state.header.hasPrevTrace).toBe(false);
    expect(state.history.traceDetail).toEqual([]);
    expect(() => state.options.queryFn({ pageParam: 0 })).toThrow(
      "Session list workspace changed",
    );
    expect(state.get).not.toHaveBeenCalled();
    state.scroll();
    expect(state.fetchNext).not.toHaveBeenCalled();
  });
  it("fails closed when a grid row has no originating context", () => {
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
        navigationContext={null}
      />,
    );
    expect(state.options.enabled).toBe(false);
    expect(() => state.options.queryFn({ pageParam: 0 })).toThrow(
      "Session list workspace changed",
    );
    expect(state.get).not.toHaveBeenCalled();
    expect(state.history.traceDetail).toEqual([]);
  });

  it.each([
    ["missing authority", undefined, context],
    [
      "missing context workspace",
      "workspace-1",
      { ...context, workspace_id: undefined },
    ],
    [
      "missing project choice",
      "workspace-1",
      { ...context, project_id: undefined },
    ],
    ["invalid filters", "workspace-1", { ...context, filters: null }],
    ["invalid sort", "workspace-1", { ...context, sort_params: null }],
  ])("hides cached rows with %s", (_name, workspace, navigationContext) => {
    state.workspace = workspace;
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
        navigationContext={navigationContext}
      />,
    );
    expect(state.options.enabled).toBe(false);
    expect(state.header.hasNextTrace).toBe(false);
    expect(state.header.hasPrevTrace).toBe(false);
    expect(state.history.traceDetail).toEqual([]);
    expect(() => state.options.queryFn({ pageParam: 0 })).toThrow();
    expect(state.get).not.toHaveBeenCalled();
    state.scroll();
    expect(state.fetchNext).not.toHaveBeenCalled();
  });

  it("preserves no-context cached/placeholder rendering without workspace authority", () => {
    state.workspace = undefined;
    state.placeholder = true;
    render(
      <TracesDrawer
        open
        onClose={vi.fn()}
        rowData={{ session_id: "session-1" }}
      />,
    );
    expect(state.options.enabled).toBe(true);
    expect(state.history.traceDetail).toEqual([{ id: "cached-row" }]);
    expect(state.header.hasNextTrace).toBe(true);
    state.scroll();
    expect(state.fetchNext).toHaveBeenCalledTimes(1);
  });

  it.each(["filters", "workspace"])(
    "hides real keepPreviousData on a %s switch without refetch loops",
    async (change) => {
      state.realQuery = true;
      const client = new QueryClient({
        defaultOptions: {
          queries: {
            placeholderData: keepPreviousData,
            staleTime: Infinity,
            retry: false,
            gcTime: Infinity,
          },
        },
      });
      state.get.mockResolvedValueOnce({
        data: {
          result: {
            response: [{ id: "old-context-row" }],
            session_metadata: { next_session_id: "session-2" },
          },
        },
      });
      const ui = (navigationContext) => (
        <QueryClientProvider client={client}>
          <TracesDrawer
            open
            onClose={vi.fn()}
            rowData={{ session_id: "session-1" }}
            navigationContext={navigationContext}
          />
        </QueryClientProvider>
      );
      const view = render(ui(context));
      try {
        await waitFor(() =>
          expect(screen.getByTestId("session-history")).toHaveTextContent(
            "old-context-row",
          ),
        );
        const initialKey = state.options.queryKey;
        view.rerender(ui(JSON.parse(JSON.stringify(context))));
        expect(state.options.queryKey).toEqual(initialKey);
        expect(state.get).toHaveBeenCalledTimes(1);
        expect(client.getQueryCache().getAll()).toHaveLength(1);
        // Pending new data: QueryObserver really supplies old data as placeholder.
        let resolveNext;
        state.get.mockImplementation(
          () =>
            new Promise((resolve) => {
              resolveNext = resolve;
            }),
        );
        if (change === "workspace") state.workspace = "workspace-2";
        view.rerender(
          ui(change === "filters" ? { ...context, filters: [] } : context),
        );
        await waitFor(() => expect(state.history.traceDetail).toEqual([]));
        expect(state.header.hasNextTrace).toBe(false);
        expect(screen.getByTestId("session-history")).not.toHaveTextContent(
          "old-context-row",
        );
        expect(state.options.enabled).toBe(change === "filters");
        state.scroll();
        expect(state.get).toHaveBeenCalledTimes(change === "filters" ? 2 : 1);
        if (change === "filters") {
          await act(async () =>
            resolveNext({
              data: {
                result: {
                  response: [{ id: "new-context-row" }],
                  session_metadata: { next_session_id: "session-3" },
                },
              },
            }),
          );
          await waitFor(() =>
            expect(screen.getByTestId("session-history")).toHaveTextContent(
              "new-context-row",
            ),
          );
          expect(state.header.hasNextTrace).toBe(true);
        }
      } finally {
        view.unmount();
        client.clear();
      }
    },
  );
});
