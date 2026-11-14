import React from "react";
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SessionsView from "../Sessions-view";

// Replaying sessions creates scenarios and a Run Simulation run, which an org
// restricted to Environments cannot open, so Sessions drops "Replay Sessions"
// from its bulk actions for such an org.

const harness = vi.hoisted(() => ({
  toolbarProps: [],
  user: {},
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
  useParams: () => ({ observeId: "project-1" }),
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
    selector({ toggledNodes: [], selectAll: false, setToggledNodes: vi.fn() }),
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

const renderView = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <SessionsView />
      </React.Suspense>
    </QueryClientProvider>,
  );

const lastBulkActionIds = async () => {
  await waitFor(() => expect(harness.toolbarProps.length).toBeGreaterThan(0));
  return harness.toolbarProps.at(-1).bulkActions.map((action) => action.id);
};

describe("Sessions replay for an org restricted to Environments", () => {
  beforeEach(() => {
    harness.toolbarProps = [];
  });

  it("drops Replay Sessions from a restricted org's bulk actions", async () => {
    harness.user = { restrict_simulate_to_environments: true };
    renderView();
    expect(await lastBulkActionIds()).toEqual(["annotation-queue"]);
  });

  it("keeps Replay Sessions for other orgs", async () => {
    harness.user = { restrict_simulate_to_environments: false };
    renderView();
    expect(await lastBulkActionIds()).toEqual(["replay", "annotation-queue"]);
  });
});
