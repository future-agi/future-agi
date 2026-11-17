// "Share with project" on a project's Users tab shares the view with the
// project as its "Default users view" tab. useSetProjectDefaultView is
// mocked here; saved-views.test.js covers its branches, name and toasts.
// A new view opens as the active tab, as on Traces; a shared open view takes
// the saved config as its baseline. The standalone Users page has no
// project, so it offers no sharing and says why.
import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  toolbar: null,
  setDefault: vi.fn(),
  setDefaultProjectIds: [],
  routeParams: { observeId: "project-1" },
  pathname: "/dashboard/observe/project-1/users",
  navigate: vi.fn(),
  emptyFilters: [],
  header: {
    activeViewConfig: null,
    setHeaderConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    registerGetViewConfig: vi.fn(),
  },
}));

vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => h.routeParams,
  useLocation: () => ({ pathname: h.pathname }),
  useNavigate: () => h.navigate,
}));
vi.mock("src/utils/axios", () => ({
  readQuery: vi.fn(),
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {
    project: {
      getUsersList: () => "/tracer/users/",
      getUsersAggregateGraphData: () => "/tracer/users/graph/",
    },
  },
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
        validatedFilters: h.emptyFilters,
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
vi.mock("../UsersGrid", () => ({ default: () => null }));
vi.mock("../UsersEmptyScreen", () => ({ default: () => null }));
vi.mock("../../LLMTracing/FilterChips", () => ({ default: () => null }));
vi.mock("../../LLMTracing/CustomColumnDialog", () => ({ default: () => null }));
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
vi.mock("src/api/project/saved-views", () => ({
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useSetProjectDefaultView: (projectId) => {
    h.setDefaultProjectIds.push(projectId);
    return h.setDefault;
  },
}));

import UsersView from "../UsersView";

const STANDALONE_SET_DEFAULT_REASON =
  "Shared views need a project. Open Users from a project.";

const SAVED_WINDOW = {
  dateOption: "Custom",
  dateFilter: ["2026-10-01 00:00:00", "2026-10-02 23:59:59"],
};
const savedConfig = (cellHeight) => ({
  extra_filters: [],
  display: { cellHeight, dateFilter: SAVED_WINDOW },
});

const setDefaultCall = () => h.setDefault.mock.calls.at(-1)[0];

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState({}, "", "/?tab=users");
  h.header.activeViewConfig = null;
  h.routeParams = { observeId: "project-1" };
  h.pathname = "/dashboard/observe/project-1/users";
  h.setDefaultProjectIds = [];
  h.toolbar = null;
});

describe("Users Share with project", () => {
  it("shares the Users view with the project and opens a new default", async () => {
    render(<UsersView />);
    await waitFor(() => expect(h.toolbar).not.toBeNull());

    expect(h.setDefaultProjectIds.at(-1)).toBe("project-1");
    expect(h.toolbar.setDefaultDisabledReason).toBeUndefined();
    act(() => h.toolbar.setCellHeight("Large"));
    await waitFor(() => expect(h.toolbar.cellHeight).toBe("Large"));
    act(() => h.toolbar.onSetDefaultView());

    expect(h.setDefault).toHaveBeenCalledTimes(1);
    expect(setDefaultCall()).toMatchObject({
      tabType: "users",
      activeViewId: null,
      config: { display: { cellHeight: "Large" }, extra_filters: [] },
    });

    act(() => setDefaultCall().onSaved({ id: "new-1" }, { created: true }));
    expect(h.navigate).toHaveBeenCalledWith(
      "/dashboard/observe/project-1/users?tab=view-new-1",
      { replace: true },
    );
    expect(h.header.setActiveViewConfig).not.toHaveBeenCalled();
  });

  it("leaves the fixed tab in place when the user's own default is updated", async () => {
    render(<UsersView />);
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
    h.header.activeViewConfig = savedConfig("Extra Large");
    render(<UsersView />);
    await waitFor(() => expect(h.toolbar?.cellHeight).toBe("Extra Large"));
    act(() => h.toolbar.onSetDefaultView());

    expect(setDefaultCall()).toMatchObject({
      tabType: "users",
      activeViewId: "saved",
      config: {
        display: { cellHeight: "Extra Large", dateFilter: SAVED_WINDOW },
      },
    });

    const shared = { id: "saved", config: savedConfig("Medium") };
    act(() => setDefaultCall().onSaved(shared, { created: false }));
    expect(h.header.setActiveViewConfig).toHaveBeenCalledWith(shared.config);

    // A response without a config keeps the config that was sent.
    act(() => setDefaultCall().onSaved({ id: "saved" }, { created: false }));
    expect(h.header.setActiveViewConfig).toHaveBeenLastCalledWith(
      setDefaultCall().config,
    );
    expect(h.navigate).not.toHaveBeenCalled();
  });

  it("offers no sharing on the standalone Users page and says why", async () => {
    h.routeParams = {};
    h.pathname = "/dashboard/users";
    window.history.replaceState({}, "", "/dashboard/users");
    render(<UsersView />);
    await waitFor(() => expect(h.toolbar).not.toBeNull());

    expect(h.toolbar.onSetDefaultView).toBeUndefined();
    expect(h.toolbar.setDefaultDisabledReason).toBe(
      STANDALONE_SET_DEFAULT_REASON,
    );
    expect(h.setDefault).not.toHaveBeenCalled();
  });
});
