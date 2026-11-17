// "Share with project" on Sessions shares the view with the project as its
// "Default sessions view" tab (useSetProjectDefaultView; its three branches
// and its toast are covered in api/project/__tests__/saved-views.test.js).
// A new one opens as the active tab, as on Traces; a shared open view takes
// the saved config as its baseline. A user's page saves personal views only,
// so it offers no sharing and says why.
import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SessionsView from "../Sessions-view";

const h = vi.hoisted(() => ({
  toolbar: null,
  grid: null,
  turnPreferences: { ready: false },
  setDefault: vi.fn(),
  setDefaultProjectIds: [],
  routeParams: { observeId: "project-1" },
  selection: { toggledNodes: [], selectAll: false, totalRowCount: 0 },
  navigate: vi.fn(),
  save: vi.fn(),
  header: {
    activeViewConfig: null,
    setHeaderConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    registerGetViewConfig: vi.fn(),
  },
}));

vi.mock("../useSessionTurnFields", () => ({
  default: () => h.turnPreferences,
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => h.routeParams,
  useNavigate: () => h.navigate,
}));
vi.mock("src/hooks/useRestrictSimulateToEnvironments", () => ({
  useRestrictSimulateToEnvironments: () => false,
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return { useUrlState: (_key, initial) => useState(initial) };
});
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => h.header,
}));
vi.mock("../../LLMTracing/useLLMTracingFilters", async () => {
  const { useState } = await import("react");
  return {
    useLLMTracingFilters: (initialFilters, initialDate) => {
      const [filters, setFilters] = useState(initialFilters);
      const [dateFilter, setDateFilter] = useState(initialDate);
      return {
        filters,
        setFilters,
        dateFilter,
        setDateFilter,
        validatedFilters: [],
      };
    },
  };
});
vi.mock("../../LLMTracing/ObserveToolbar", () => ({
  default: (props) => {
    h.toolbar = props;
    return null;
  },
}));
vi.mock("../../LLMTracing/GraphSection/PrimaryGraph", () => ({
  default: () => null,
}));
vi.mock("../Session-grid", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function MockSessionGrid(props, _ref) {
      h.grid = props;
      return null;
    }),
  };
});
vi.mock("../../UsersView/useProjectFilterField", () => ({
  default: () => null,
}));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/SelectAllBanner", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({ default: () => null }));
vi.mock(
  "src/sections/annotations/queues/components/add-to-queue-dialog",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);
vi.mock("../../LLMTracing/useCursorAttributeInventory", () => ({
  useCursorAttributeInventory: () => ({
    attributes: [],
    inventoryControlProps: {},
  }),
}));
vi.mock("../ReplaySessions/store", () => ({
  useReplaySessionsStoreShallow: (selector) =>
    selector({
      openReplaySessionDrawer: {},
      setCreatedReplay: vi.fn(),
      setReplayType: vi.fn(),
      setOpenReplaySessionDrawer: vi.fn(),
    }),
  useSessionsGridStore: {
    setState: (state) => Object.assign(h.selection, state),
  },
  useSessionsGridStoreShallow: (selector) => selector(h.selection),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const original = await importOriginal();
  return {
    ...original,
    default: {
      ...original.default,
      post: () => Promise.resolve({ data: {} }),
    },
  };
});
vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({ mutate: vi.fn() }),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useUpdateSavedView: () => ({ mutate: h.save }),
  useUpdateWorkspaceSavedView: () => ({ mutate: h.save }),
  useSetProjectDefaultView: (projectId) => {
    h.setDefaultProjectIds.push(projectId);
    return h.setDefault;
  },
}));

const USER_MODE_SET_DEFAULT_REASON =
  "Shared views need a project. Open the sessions from their project.";

const SAVED_WINDOW = {
  dateOption: "Custom",
  dateFilter: ["2026-10-01 00:00:00", "2026-10-02 23:59:59"],
};
const savedConfig = (cellHeight) => ({
  extra_filters: [],
  display: { cellHeight, showCompare: false, dateFilter: SAVED_WINDOW },
});

const mount = (props = {}) =>
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

const setDefaultCall = () => h.setDefault.mock.calls.at(-1)[0];

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState({}, "", "/?tab=sessions");
  h.header.activeViewConfig = null;
  h.routeParams = { observeId: "project-1" };
  h.selection = { toggledNodes: [], selectAll: false, totalRowCount: 0 };
  h.setDefaultProjectIds = [];
  h.toolbar = null;
  h.grid = null;
  h.turnPreferences = { ready: false };
});
afterEach(cleanup);

describe("Sessions Share with project", () => {
  it("includes turn fields in saved configs and dirty state and passes the complete current view when saving", async () => {
    window.history.replaceState({}, "", "/?tab=view-saved");
    h.header.activeViewConfig = {
      ...savedConfig("Short"),
      columns: ["input"],
      display: {
        ...savedConfig("Short").display,
        sessionTurnFields: [],
        retained: "keep",
      },
    };
    const fields = [{ kind: "value", label: "Answer", path: "output.answer" }];
    const save = vi.fn().mockResolvedValue({
      config: h.header.activeViewConfig,
      savedActiveView: true,
    });
    h.turnPreferences = { projectId: "project-1", ready: true, fields, save };
    mount();
    await waitFor(() => expect(h.toolbar?.dateFilter).toEqual(SAVED_WINDOW));
    expect(h.toolbar.canSaveView).toBe(true);
    const config = h.header.registerGetViewConfig.mock.calls.at(-1)[0]();
    expect(config).toMatchObject({
      columns: ["input"],
      display: { retained: "keep", sessionTurnFields: fields },
    });
    expect(h.grid.turnFieldPreferences.fields).toEqual(fields);
    await act(async () => {
      await h.grid.turnFieldPreferences.save([]);
    });
    expect(save).toHaveBeenCalledWith(
      [],
      expect.objectContaining({
        columns: ["input"],
        display: expect.objectContaining({
          retained: "keep",
          cellHeight: "Short",
        }),
      }),
    );
    expect(h.header.setActiveViewConfig).toHaveBeenCalledWith(
      h.header.activeViewConfig,
    );
  });

  it("shares the Sessions view with the project and opens a new default", async () => {
    mount();
    await waitFor(() => expect(h.toolbar).not.toBeNull());

    expect(h.setDefaultProjectIds.at(-1)).toBe("project-1");
    expect(h.toolbar.setDefaultDisabledReason).toBeUndefined();
    act(() => h.toolbar.setCellHeight("Large"));
    await waitFor(() => expect(h.toolbar.cellHeight).toBe("Large"));
    act(() => h.toolbar.onSetDefaultView());

    expect(h.setDefault).toHaveBeenCalledTimes(1);
    expect(setDefaultCall()).toMatchObject({
      tabType: "sessions",
      activeViewId: null,
      config: {
        display: { cellHeight: "Large", showCompare: false },
        extra_filters: [],
      },
    });

    act(() => setDefaultCall().onSaved({ id: "new-1" }, { created: true }));
    expect(h.navigate).toHaveBeenCalledWith(
      "/dashboard/observe/project-1/sessions?tab=view-new-1",
      { replace: true },
    );
    expect(h.header.setActiveViewConfig).not.toHaveBeenCalled();
  });

  it("leaves the fixed tab in place when the user's own default is updated", async () => {
    mount();
    await waitFor(() => expect(h.toolbar).not.toBeNull());
    act(() => h.toolbar.onSetDefaultView());

    act(() =>
      setDefaultCall().onSaved({ id: "own-default" }, { created: false }),
    );

    expect(h.navigate).not.toHaveBeenCalled();
    expect(h.header.setActiveViewConfig).not.toHaveBeenCalled();
  });

  it("shares the open saved view and takes the saved config as its baseline", async () => {
    window.history.replaceState({}, "", "/?tab=view-saved");
    h.header.activeViewConfig = savedConfig("Short");
    mount();
    await waitFor(() => expect(h.toolbar?.dateFilter).toEqual(SAVED_WINDOW));
    act(() => h.toolbar.onSetDefaultView());

    expect(setDefaultCall()).toMatchObject({
      tabType: "sessions",
      activeViewId: "saved",
      config: { display: { dateFilter: SAVED_WINDOW } },
    });

    const shared = { id: "saved", config: savedConfig("Medium") };
    act(() => setDefaultCall().onSaved(shared, { created: false }));
    expect(h.header.setActiveViewConfig).toHaveBeenCalledWith(shared.config);
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it("offers no shared default on a user's page and says why", async () => {
    h.routeParams = { userId: "user-1" };
    mount({ mode: "user", userIdForUserMode: "user-1" });
    await waitFor(() => expect(h.toolbar).not.toBeNull());

    expect(h.toolbar.onSetDefaultView).toBeUndefined();
    expect(h.toolbar.setDefaultDisabledReason).toBe(
      USER_MODE_SET_DEFAULT_REASON,
    );
    expect(h.setDefault).not.toHaveBeenCalled();
  });
});
