import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SessionsView from "../Sessions-view";

// OBS-SW-172: on a user's detail page, Group by Trace/Span leaves Sessions for
// the user's Trace/Span grid. The picked date window has to travel with it;
// the destination otherwise reopens at its own default window.

const harness = vi.hoisted(() => ({
  toolbarProps: [],
  navigate: vi.fn(),
  user: { restrict_simulate_to_environments: false },
  routeParams: {},
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
  useNavigate: () => harness.navigate,
  useParams: () => harness.routeParams,
}));
vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
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
    selector({
      toggledNodes: [],
      selectAll: false,
      totalRowCount: 0,
      setToggledNodes: vi.fn(),
    }),
}));
vi.mock("src/api/project/replay-sessions", () => ({
  useCreateReplaySessions: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
  useGetSavedViews: () => ({ data: { custom_views: [] } }),
}));

const PICKED_WINDOW = {
  dateFilter: ["2026-10-01T00:00:00.000Z", "2026-10-15T00:00:00.000Z"],
  dateOption: "Custom",
};

const renderUserSessions = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <SessionsView mode="user" userIdForUserMode="user 1" />
      </React.Suspense>
    </QueryClientProvider>,
  );

describe("Sessions user-mode Group by keeps the picked date window", () => {
  beforeEach(() => {
    harness.toolbarProps = [];
    harness.routeParams = { userId: "user 1" };
    harness.navigate.mockClear();
    harness.observeHeader.setActiveViewConfig.mockClear();
  });

  it.each([
    ["trace", "primaryTraceDateFilter", null],
    ["none", "primaryTraceDateFilter", null],
    ["span", "primarySpanDateFilter", "spans"],
  ])(
    "Group by %s carries the window into %s",
    async (groupKey, dateKey, selectedTab) => {
      renderUserSessions();
      await waitFor(() =>
        expect(harness.toolbarProps.length).toBeGreaterThan(0),
      );
      act(() => harness.toolbarProps.at(-1).setDateFilter(PICKED_WINDOW));
      await waitFor(() =>
        expect(harness.toolbarProps.at(-1).dateFilter).toEqual(PICKED_WINDOW),
      );
      harness.navigate.mockClear();

      act(() => harness.toolbarProps.at(-1).onGroupByChange(groupKey));

      expect(harness.observeHeader.setActiveViewConfig).toHaveBeenCalledWith(
        null,
      );
      expect(harness.navigate).toHaveBeenCalledTimes(1);
      const [{ pathname, search }] = harness.navigate.mock.calls[0];
      expect(pathname).toBe("/dashboard/users/user%201");
      const params = new URLSearchParams(search);
      expect(params.get("userTab")).toBe("traces");
      expect(params.get("selectedTab")).toBe(selectedTab);
      expect(JSON.parse(params.get(dateKey))).toEqual(PICKED_WINDOW);
      // Sessions keeps its own window too, so the tab bar can carry it back.
      expect(JSON.parse(params.get("sessionDateFilter"))).toEqual(
        PICKED_WINDOW,
      );
      expect([...params.keys()].sort()).toEqual(
        [
          "userTab",
          "sessionDateFilter",
          dateKey,
          ...(selectedTab ? ["selectedTab"] : []),
        ].sort(),
      );
    },
  );
});
