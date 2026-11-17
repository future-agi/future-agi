import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SessionsView from "../Sessions-view";
import {
  EVIDENCE_ACTION_ID,
  EVIDENCE_COPY,
} from "src/sections/simulate/environments/evidence/evidence.constants";
import { MAX_EVIDENCE_SOURCES } from "src/api/simulate-environments/evidenceImports";

// Replaying sessions creates scenarios and a Run Simulation run, which an org
// restricted to Environments cannot open, so Sessions drops "Replay Sessions"
// from its bulk actions for such an org. "Create environment test" builds a
// test through an environment, so every org keeps it.

const harness = vi.hoisted(() => ({
  toolbarProps: [],
  user: {},
  grid: { toggledNodes: [], selectAll: false },
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
    selector({ ...harness.grid, setToggledNodes: vi.fn() }),
}));
vi.mock(
  "src/sections/simulate/environments/evidence/CreateEnvironmentTestDialog",
  () => ({
    default: (props) => {
      harness.evidenceDialogProps = props;
      return null;
    },
  }),
);
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

const lastBulkActions = async () => {
  await waitFor(() => expect(harness.toolbarProps.length).toBeGreaterThan(0));
  return harness.toolbarProps.at(-1).bulkActions;
};
const lastBulkActionIds = async () =>
  (await lastBulkActions()).map((action) => action.id);
const evidenceAction = async () =>
  (await lastBulkActions()).find((action) => action.id === EVIDENCE_ACTION_ID);

describe("Sessions replay for an org restricted to Environments", () => {
  beforeEach(() => {
    harness.toolbarProps = [];
    harness.grid = { toggledNodes: [], selectAll: false };
  });

  it("drops Replay Sessions from a restricted org's bulk actions", async () => {
    harness.user = { restrict_simulate_to_environments: true };
    renderView();
    expect(await lastBulkActionIds()).toEqual([
      "annotation-queue",
      EVIDENCE_ACTION_ID,
    ]);
  });

  it("keeps Replay Sessions for other orgs", async () => {
    harness.user = { restrict_simulate_to_environments: false };
    renderView();
    expect(await lastBulkActionIds()).toEqual([
      "replay",
      "annotation-queue",
      EVIDENCE_ACTION_ID,
    ]);
  });
});

describe("Sessions create environment test", () => {
  beforeEach(() => {
    harness.toolbarProps = [];
    harness.user = { restrict_simulate_to_environments: true };
  });

  it("takes picked sessions", async () => {
    harness.grid = { toggledNodes: ["s1", "s2"], selectAll: false };
    renderView();
    expect(await evidenceAction()).toMatchObject({
      disabled: false,
      disabledReason: null,
    });
  });

  it("refuses select-all: its ids are the ones left out", async () => {
    harness.grid = { toggledNodes: ["s1"], selectAll: true };
    renderView();
    expect(await evidenceAction()).toMatchObject({
      disabled: true,
      disabledReason: EVIDENCE_COPY.actionAll,
    });
  });

  it("refuses more sessions than one import takes", async () => {
    harness.grid = {
      toggledNodes: Array.from(
        { length: MAX_EVIDENCE_SOURCES + 1 },
        (_, i) => `s${i}`,
      ),
      selectAll: false,
    };
    renderView();
    expect(await evidenceAction()).toMatchObject({
      disabled: true,
      disabledReason: EVIDENCE_COPY.actionTooMany,
    });
  });

  it("opens the dialog with this project's picked sessions", async () => {
    harness.grid = { toggledNodes: ["s1", null, "s2"], selectAll: false };
    harness.evidenceDialogProps = null;
    renderView();
    await evidenceAction();
    act(() => harness.toolbarProps.at(-1).onBulkAction(EVIDENCE_ACTION_ID));
    await waitFor(() =>
      expect(harness.evidenceDialogProps).toMatchObject({
        open: true,
        projectId: "project-1",
        sourceKind: "session",
        ids: ["s1", "s2"],
      }),
    );
  });
});
