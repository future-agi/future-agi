import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render as renderWithTheme } from "src/utils/test-utils";
import BulkActionsBar from "src/sections/projects/LLMTracing/BulkActionsBar";
import SessionsView from "../Sessions-view";

// Replaying sessions creates scenarios and a Run Simulation run, which an org
// restricted to Environments cannot open, so Sessions drops "Replay Sessions"
// from its bulk actions for such an org.

const harness = vi.hoisted(() => ({
  toolbarProps: [],
  user: {},
  routeParams: {},
  selection: {},
  createReplaySessions: vi.fn(),
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin", user: harness.user }),
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => harness.routeParams,
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const ReactModule = await import("react");
  return {
    useUrlState: (_key, defaultValue) => ReactModule.useState(defaultValue),
  };
});
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => harness.observeHeader,
}));
vi.mock("src/sections/projects/LLMTracing/useLLMTracingFilters", async () => {
  const ReactModule = await import("react");
  return {
    useLLMTracingFilters: (defaultFilters, defaultDateFilter) => {
      const [filters, setFilters] = ReactModule.useState(defaultFilters);
      const [dateFilter, setDateFilter] =
        ReactModule.useState(defaultDateFilter);
      return {
        filters,
        setFilters,
        validatedFilters: [],
        dateFilter,
        setDateFilter,
      };
    },
  };
});
vi.mock("src/sections/projects/LLMTracing/ObserveToolbar", () => ({
  default: (props) => {
    harness.toolbarProps.push(props);
    return null;
  },
}));
vi.mock("src/sections/projects/LLMTracing/FilterChips", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/SelectAllBanner", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/CustomColumnDialog", () => ({
  default: () => null,
}));
vi.mock("../Session-grid", () => ({ default: () => null }));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);
vi.mock("src/sections/projects/UsersView/useProjectFilterField", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({ data: [] }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/sections/projects/SessionsView/ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      setIsReplayDrawerCollapsed: vi.fn(),
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    }),
  useSessionsGridStore: Object.assign(
    (selector) => selector({ toggledNodes: [], setToggledNodes: vi.fn() }),
    { getState: () => ({ setToggledNodes: vi.fn() }) },
  ),
  useSessionsGridStoreShallow: (selector) =>
    selector({ ...harness.selection, setToggledNodes: vi.fn() }),
}));
vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({
    mutate: harness.createReplaySessions,
    isPending: false,
  }),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
}));

const renderView = (props = {}) =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <SessionsView {...props} />
      </React.Suspense>
    </QueryClientProvider>,
  );

const lastBulkActionIds = async () => {
  await waitFor(() => expect(harness.toolbarProps.length).toBeGreaterThan(0));
  return harness.toolbarProps.at(-1).bulkActions.map((action) => action.id);
};

// Only Replay Sessions depends on the restriction; "add_to_dataset" (the DSX
// Add-data sheet, R2 P5) is offered to every org.
describe("Sessions replay availability", () => {
  beforeEach(() => {
    harness.toolbarProps = [];
    harness.user = { restrict_simulate_to_environments: false };
    harness.routeParams = {
      observeId: "11111111-1111-4111-8111-111111111111",
    };
    harness.selection = {
      toggledNodes: ["22222222-2222-4222-8222-222222222222"],
      selectAll: false,
      totalRowCount: 2,
    };
    harness.createReplaySessions.mockClear();
  });

  it("drops Replay Sessions from a restricted org's bulk actions", async () => {
    harness.user = { restrict_simulate_to_environments: true };
    renderView();
    expect(await lastBulkActionIds()).toEqual([
      "add_to_dataset",
      "annotation-queue",
    ]);
  });

  it("keeps Replay Sessions for other orgs", async () => {
    harness.user = { restrict_simulate_to_environments: false };
    renderView();
    expect(await lastBulkActionIds()).toEqual([
      "replay",
      "add_to_dataset",
      "annotation-queue",
    ]);
  });

  // W10-TRACING-OTHER-L03: user detail has no project-hosted replay flow, so
  // Replay Sessions stays listed there but disabled, with the reason.
  it.each([false, true])(
    "lists Replay Sessions disabled with the reason in user mode (single-project filter: %s)",
    async (filterToProject) => {
      const projectId = harness.routeParams.observeId;
      harness.routeParams = { userId: "user-1" };
      renderView({ mode: "user", userIdForUserMode: "user-1" });
      await lastBulkActionIds();

      if (filterToProject) {
        act(() => {
          harness.toolbarProps.at(-1).onApplyExtraFilters([
            {
              column_id: "project_id",
              filter_config: {
                filter_type: "text",
                filter_op: "equals",
                filter_value: projectId,
              },
            },
          ]);
        });
        expect(harness.toolbarProps.at(-1).projectId).toBe(projectId);
      }

      expect(harness.toolbarProps.at(-1).selectedCount).toBe(1);
      expect(await lastBulkActionIds()).toEqual([
        "replay",
        "add_to_dataset",
        "annotation-queue",
      ]);
      const [replay, ...others] = harness.toolbarProps.at(-1).bulkActions;
      expect(replay).toMatchObject({
        id: "replay",
        label: "Replay Sessions",
        disabled: true,
        disabledReason:
          "Replay needs a project. Open the session from its project to replay it.",
      });
      for (const action of others) expect(action.disabled).toBeFalsy();
      expect(harness.createReplaySessions).not.toHaveBeenCalled();
    },
  );

  it("keeps Replay Sessions enabled in project mode", async () => {
    renderView();
    await lastBulkActionIds();
    const replay = harness.toolbarProps
      .at(-1)
      .bulkActions.find((action) => action.id === "replay");
    expect(replay.disabled).toBeFalsy();
    expect(replay.disabledReason).toBeUndefined();
  });

  it.each([false, true])(
    "does not submit a user-mode replay without a project (select all: %s)",
    async (selectAll) => {
      harness.routeParams = { userId: "user-1" };
      harness.selection.selectAll = selectAll;
      renderView({ mode: "user", userIdForUserMode: "user-1" });
      await lastBulkActionIds();

      act(() => harness.toolbarProps.at(-1).onBulkAction("replay"));

      expect(harness.createReplaySessions).not.toHaveBeenCalled();
    },
  );

  // ObserveToolbar (mocked here) hands these same props to the real bulk
  // menu: bulkActions as actions and onBulkAction as onAction.
  const openBulkMenu = async () => {
    await lastBulkActionIds();
    const toolbar = harness.toolbarProps.at(-1);
    renderWithTheme(
      <BulkActionsBar
        selectedCount={toolbar.selectedCount}
        onClearSelection={() => {}}
        onAction={toolbar.onBulkAction}
        actions={toolbar.bulkActions}
      />,
    );
    fireEvent.click(screen.getByText("Actions"));
    return screen.findByRole("menuitem", { name: "Replay Sessions" });
  };

  it("lets a keyboard user reach the user-mode Replay Sessions reason without replaying", async () => {
    harness.routeParams = { userId: "user-1" };
    renderView({ mode: "user", userIdForUserMode: "user-1" });
    const replay = await openBulkMenu();

    // Replay Sessions is the first item, so it takes focus on open.
    await waitFor(() => expect(replay).toHaveFocus());
    const add = screen.getByRole("menuitem", { name: "Add to dataset" });
    fireEvent.keyDown(replay, { key: "ArrowDown" });
    expect(add).toHaveFocus();
    fireEvent.keyDown(add, { key: "ArrowUp" });
    expect(replay).toHaveFocus();
    expect(replay).toHaveAttribute("aria-disabled", "true");
    await waitFor(() =>
      expect(replay).toHaveAccessibleDescription(
        "Replay needs a project. Open the session from its project to replay it.",
      ),
    );

    fireEvent.keyDown(replay, { key: "Enter" });
    replay.click();
    expect(harness.createReplaySessions).not.toHaveBeenCalled();
  });

  it("replays from the project-mode bulk menu by keyboard", async () => {
    renderView();
    const replay = await openBulkMenu();
    expect(replay).not.toHaveAttribute("aria-disabled");
    await waitFor(() => expect(replay).toHaveFocus());
    fireEvent.keyDown(replay, { key: "Enter" });
    expect(harness.createReplaySessions).toHaveBeenCalledOnce();
    expect(harness.createReplaySessions.mock.calls[0][0]).toMatchObject({
      project_id: harness.routeParams.observeId,
      replay_type: "session",
    });
  });

  // Project mode keeps the OBS-SW-36 replay request: the project, the view's
  // active filters (none under this file's filter mock), and, under select
  // all, the toggled rows as exclusions instead of ids. The API rejects a
  // session select-all without filters.
  it.each([false, true])(
    "preserves project-mode replay payloads (select all: %s)",
    async (selectAll) => {
      harness.selection.selectAll = selectAll;
      renderView();
      expect(await lastBulkActionIds()).toContain("replay");

      act(() => harness.toolbarProps.at(-1).onBulkAction("replay"));

      expect(harness.createReplaySessions).toHaveBeenCalledExactlyOnceWith(
        {
          project_id: harness.routeParams.observeId,
          replay_type: "session",
          ids: selectAll ? [] : harness.selection.toggledNodes,
          select_all: selectAll,
          exclude_ids: selectAll ? harness.selection.toggledNodes : [],
          filters: [],
        },
        expect.objectContaining({
          onSuccess: expect.any(Function),
          onError: expect.any(Function),
        }),
      );
    },
  );
});
