import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SessionsView from "../SessionsView/Sessions-view";
import UsersView from "../UsersView/UsersView";
import useUsersStore from "../UsersView/Store/usersStore";

// Exercise the real components' dirty checks and save handlers.
// Only the surrounding UI, transport and URL/filter storage are substituted;
// saved-view comparison helpers and the Users column store remain real.
const harness = vi.hoisted(() => ({
  initialConfig: null,
  toolbarProps: null,
  emptyFilters: [],
  updateSavedView: vi.fn(),
  updateWorkspaceSavedView: vi.fn(),
  registerGetViewConfig: vi.fn(),
  setHeaderConfig: vi.fn(),
  replayState: {
    openReplaySessionDrawer: {},
    setReplayType: vi.fn(),
    setOpenReplaySessionDrawer: vi.fn(),
    setCreatedReplay: vi.fn(),
  },
  sessionGridState: {
    toggledNodes: [],
    selectAll: false,
    totalRowCount: 0,
    totalRowCountLowerBound: 0,
    totalRowCountIsLowerBound: false,
    setToggledNodes: vi.fn(),
  },
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", () => ({
  useParams: () => ({ observeId: "project-1" }),
  useLocation: () => ({ pathname: "/dashboard/observe/project-1" }),
  useNavigate: () => vi.fn(),
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/sections/project/context/ObserveHeaderContext", async () => {
  const { useState } = await import("react");
  return {
    useObserveHeader: () => {
      const [activeViewConfig, setActiveViewConfig] = useState(
        harness.initialConfig,
      );
      return {
        activeViewConfig,
        setActiveViewConfig,
        registerGetViewConfig: harness.registerGetViewConfig,
        setHeaderConfig: harness.setHeaderConfig,
      };
    },
  };
});
vi.mock("../LLMTracing/useLLMTracingFilters", async () => {
  const { useState } = await import("react");
  return {
    useLLMTracingFilters: (initialFilters, initialDateFilter) => {
      const [filters, setFilters] = useState(initialFilters);
      const [dateFilter, setDateFilter] = useState(initialDateFilter);
      return {
        filters,
        setFilters,
        validatedFilters: harness.emptyFilters,
        dateFilter,
        setDateFilter,
      };
    },
  };
});
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: harness.updateSavedView }),
  useUpdateWorkspaceSavedView: () => ({
    mutate: harness.updateWorkspaceSavedView,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/utils/Mixpanel", () => ({ Events: {}, trackEvent: vi.fn() }));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/utils/axios", () => ({
  default: { post: vi.fn() },
  endpoints: {
    project: {
      updateSessionListColumnVisibility: () => "/session-columns/",
      getSessionGraphData: () => "/sessions/graph/",
      getUsersAggregateGraphData: () => "/users/graph/",
    },
  },
}));
vi.mock("src/sections/common/AddData/addDataStore", () => ({
  openAddData: vi.fn(),
}));
vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({ mutate: vi.fn() }),
}));
vi.mock("../SessionsView/ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) => selector(harness.replayState),
  useSessionsGridStoreShallow: (selector) => selector(harness.sessionGridState),
  useSessionsGridStore: {
    getState: () => harness.sessionGridState,
    setState: vi.fn(),
  },
}));
vi.mock("../SessionsView/common", () => ({
  initialVisibility: { session_id: true },
  resolveColumnVisibility: (column, visibility) => visibility[column.id],
}));
vi.mock("../UsersView/common", async (importOriginal) => ({
  ...(await importOriginal()),
  getUsersColumnConfig: () => [],
  userDefaultFilter: {},
}));
vi.mock("../UsersView/useProjectFilterField", () => ({ default: () => null }));
vi.mock("../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../LLMTracing/ObserveToolbar", () => ({
  default: (props) => {
    harness.toolbarProps = props;
    return null;
  },
}));
vi.mock("../SessionsView/Session-grid", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef(() => null) };
});
vi.mock("../UsersView/UsersGrid", () => ({ default: () => null }));
vi.mock("../UsersView/UsersEmptyScreen", () => ({ default: () => null }));
vi.mock("../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: () => null,
}));
vi.mock("../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../LLMTracing/SelectAllBanner", () => ({ default: () => null }));
vi.mock("../LLMTracing/CustomColumnDialog", () => ({ default: () => null }));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

const originalBounds = ["2026-11-01 00:00:00", "2026-11-02 23:59:59"];
const changedBounds = ["2026-11-03 00:00:00", "2026-11-04 23:59:59"];
const custom = (bounds) => ({ dateOption: "Custom", dateFilter: [...bounds] });
const savedConfig = (dateFilter) => ({
  extra_filters: [],
  display: { cellHeight: "Short", dateFilter },
});
const queryClients = [];

const expectCanSave = async (expected) => {
  await waitFor(() => {
    expect(harness.toolbarProps).not.toBeNull();
    expect(harness.toolbarProps.canSaveView).toBe(expected);
  });
};

const renderSavedView = async (View) => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queryClients.push(client);
  const view = render(
    <QueryClientProvider client={client}>
      <View />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(harness.toolbarProps?.dateFilter).toEqual(
      harness.initialConfig.display.dateFilter,
    ),
  );
  await expectCanSave(false);
  return view;
};

beforeEach(() => {
  vi.clearAllMocks();
  harness.initialConfig = savedConfig(custom(originalBounds));
  harness.toolbarProps = null;
  useUsersStore.setState({ columns: [], gridApi: null });
  window.history.replaceState(
    {},
    "",
    "/dashboard/observe/project-1?tab=view-saved-range",
  );
  harness.updateSavedView.mockImplementation(({ config }, { onSuccess }) => {
    onSuccess({ data: { result: { config } } });
  });
});

afterEach(() => {
  cleanup();
  queryClients.splice(0).forEach((client) => client.clear());
});

describe.each([
  ["Sessions", SessionsView],
  ["Users", UsersView],
])("H12-C25-001 %s saved-view dates", (_name, View) => {
  it.each([
    ["start-only", ["2026-11-01 12:00:00", originalBounds[1]]],
    ["end-only", [originalBounds[0], changedBounds[1]]],
    ["both-bound", changedBounds],
  ])(
    "enables saving a Custom %s edit and restores the saved dates",
    async (_edit, bounds) => {
      const view = await renderSavedView(View);
      const nextDateFilter = custom(bounds);

      act(() => harness.toolbarProps.setDateFilter(nextDateFilter));
      await expectCanSave(true);
      expect(harness.updateSavedView).not.toHaveBeenCalled();
      expect(harness.initialConfig.display.dateFilter).toEqual(
        custom(originalBounds),
      );

      act(() => harness.toolbarProps.onSaveView());
      expect(harness.updateSavedView).toHaveBeenCalledTimes(1);
      const [payload] = harness.updateSavedView.mock.calls[0];
      expect(payload.id).toBe("saved-range");
      expect(payload.config.display.dateFilter).toEqual(nextDateFilter);
      expect(harness.updateWorkspaceSavedView).not.toHaveBeenCalled();
      await expectCanSave(false);

      view.unmount();
      harness.initialConfig = payload.config;
      harness.toolbarProps = null;
      await renderSavedView(View);
      expect(harness.toolbarProps.dateFilter).toEqual(nextDateFilter);
    },
  );

  it("keeps unchanged Custom bounds clean even in a new array", async () => {
    await renderSavedView(View);
    act(() => harness.toolbarProps.setDateFilter(custom(originalBounds)));
    await expectCanSave(false);
    expect(harness.updateSavedView).not.toHaveBeenCalled();
  });

  it("clears the dirty state when Custom bounds are reverted", async () => {
    await renderSavedView(View);
    act(() => harness.toolbarProps.setDateFilter(custom(changedBounds)));
    await expectCanSave(true);
    act(() => harness.toolbarProps.setDateFilter(custom(originalBounds)));
    await expectCanSave(false);
  });

  it.each(["Today", "7D", "30D"])(
    "ignores rolling %s bounds when the preset is unchanged",
    async (dateOption) => {
      harness.initialConfig = savedConfig({
        dateOption,
        dateFilter: [...originalBounds],
      });
      await renderSavedView(View);
      act(() =>
        harness.toolbarProps.setDateFilter({
          dateOption,
          dateFilter: [...changedBounds],
        }),
      );
      await expectCanSave(false);
    },
  );

  it("still detects a change from Custom to Today", async () => {
    await renderSavedView(View);
    act(() =>
      harness.toolbarProps.setDateFilter({
        dateOption: "Today",
        dateFilter: [...originalBounds],
      }),
    );
    await expectCanSave(true);
  });

  it("still detects a row-height-only edit", async () => {
    await renderSavedView(View);
    act(() => harness.toolbarProps.setCellHeight("Tall"));
    await expectCanSave(true);
  });

  it.each([[undefined], [[]]])(
    "preserves legacy Custom views without saved bounds (%j)",
    async (dateFilter) => {
      harness.initialConfig = savedConfig({ dateOption: "Custom", dateFilter });
      await renderSavedView(View);
      act(() => harness.toolbarProps.setDateFilter(custom(changedBounds)));
      await expectCanSave(false);
    },
  );
});
