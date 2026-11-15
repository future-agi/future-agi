import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LLMTracingView from "../LLMTracingView";

// Keep the real view, lazy AgentGraph/AgentPath, and error presentation. Only
// unrelated screen dependencies and the network hook's results are isolated.
const harness = vi.hoisted(() => ({
  viewMode: "agentGraph",
  query: {},
  gridProps: [],
  filters: [
    {
      column_id: "tag",
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_op: "equals",
        filter_value: "wanted",
      },
    },
  ],
  header: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
}));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Admin" }),
}));
vi.mock("react-helmet-async", () => ({ Helmet: () => null }));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ observeId: "project-1" }),
}));
vi.mock("src/routes/hooks/use-url-state", async () => {
  const { useState } = await import("react");
  return {
    useUrlState: (key, initial) =>
      useState(key === "showCompare" ? true : initial),
  };
});
vi.mock("src/sections/project/context/ObserveHeaderContext", () => ({
  useObserveHeader: () => harness.header,
}));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "observe" } }),
}));
vi.mock("../useLLMTracingFilters", async () => {
  const { useState } = await import("react");
  return {
    useLLMTracingFilters: (initial, initialDate) => {
      const [filters, setFilters] = useState(initial);
      const [dateFilter, setDateFilter] = useState(initialDate);
      return {
        filters,
        setFilters,
        validatedFilters: harness.filters,
        dateFilter,
        setDateFilter,
      };
    },
  };
});
vi.mock("../states", () => {
  const grid = {
    toggledNodes: [],
    selectAll: false,
    totalRowCount: 0,
    totalRowCountLowerBound: 0,
    totalRowCountIsLowerBound: false,
  };
  return {
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) =>
      selector({
        resetStates: vi.fn(),
        viewMode: harness.viewMode,
        setViewMode: vi.fn(),
      }),
    useTraceGridStoreShallow: (selector) => selector(grid),
    useSpanGridStoreShallow: (selector) => selector(grid),
  };
});
vi.mock("../TraceGrid", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef((props, _ref) => {
      harness.gridProps.push(props);
      return <div data-testid="filtered-list">Filtered list</div>;
    }),
  };
});
vi.mock("../SpanGrid", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef((_props, _ref) => null) };
});
vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async () => {
  const { forwardRef } = await import("react");
  return { default: forwardRef((_props, _ref) => null) };
});
vi.mock("../ObserveToolbar", () => ({ default: () => null }));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useAnnotationQueuesList: () => ({ data: { results: [] }, isLoading: false }),
  useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("src/sections/annotations/queues/create-queue-drawer", () => ({
  default: () => null,
}));
vi.mock("src/sections/test-detail/states", () => ({
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector({ setTestDetailDrawerOpen: vi.fn() }),
}));
vi.mock("src/sections/projects/UsersView/useProjectFilterField", () => ({
  default: () => null,
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: () => ({ data: [] }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));
vi.mock("src/api/project/agent-graph", () => ({
  useAgentGraph: () => harness.query,
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
  useSessionsGridStore: { getState: () => ({ setToggledNodes: vi.fn() }) },
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
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: {
    dashboard: { metrics: "/tracer/dashboard/metrics/" },
    project: {
      addAnnotationValuesForSpan: () => "/annotations/values/",
      getSpanGraphData: () => "/spans/graph/",
      getTraceGraphData: () => "/traces/graph/",
      getTrace: () => "/traces/detail/",
      spanAttributeKeys: () => "/traces/span-attribute-keys/",
      updateProjectColumnVisibility: () => "/projects/columns/",
    },
  },
}));
vi.mock("../GraphSection/PrimaryGraph", () => ({ default: () => null }));
vi.mock("../SelectAllBanner", () => ({ default: () => null }));
vi.mock("../FilterChips", () => ({ default: () => null }));
vi.mock("../TracingControls", () => ({ default: () => null }));
vi.mock("../CustomColumnDialog", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));
vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));
vi.mock("src/components/traceDetail/AddTagsPopover", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/addToDataset/add-dataset", () => ({
  default: () => null,
}));
vi.mock("src/components/traceDetailDrawer/AnnotateDrawer", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  () => ({ default: () => null }),
);

const renderView = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <React.Suspense fallback={null}>
        <LLMTracingView />
      </React.Suspense>
    </QueryClientProvider>,
  );

const permanentMessage =
  "This graph cannot apply this Tag filter. The list remains filtered. Narrow the time range or remove the Tag filter.";
const rejection = { code: "tag_filter_too_broad", message: permanentMessage };

describe.each(["agentGraph", "agentPath"])(
  "%s primary and compare query errors",
  (viewMode) => {
    beforeEach(() => {
      harness.viewMode = viewMode;
      harness.gridProps = [];
      harness.query = {
        data: undefined,
        isLoading: false,
        isError: true,
        pollingPaused: false,
      };
    });

    it.each([
      ["flattened", { ...rejection, statusCode: 400 }],
      ["wrapped", { response: { status: 400, data: rejection } }],
    ])(
      "renders permanent %s error copy in both panes, not retry guidance",
      async (_, error) => {
        harness.query.error = error;
        renderView();
        await waitFor(() =>
          expect(screen.getAllByText(permanentMessage)).toHaveLength(2),
        );
        expect(
          screen.queryByText(/Please retry in a moment/),
        ).not.toBeInTheDocument();
        expect(screen.getAllByTestId("filtered-list")[0]).toBeVisible();
        expect(
          harness.gridProps.findLast((props) => props.enabled).filters,
        ).toEqual(expect.arrayContaining(harness.filters));
      },
    );

    it("keeps transient typed 503 retry guidance in both panes", async () => {
      harness.query.error = {
        statusCode: 503,
        code: "query_failed",
        retryable: true,
        message: "private transport detail",
      };
      renderView();
      await waitFor(() =>
        expect(screen.getAllByText(/Please retry in a moment/)).toHaveLength(2),
      );
      expect(
        screen.queryByText("private transport detail"),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(permanentMessage)).not.toBeInTheDocument();
    });

    it("renders a valid graph in both panes without error guidance", async () => {
      harness.query = {
        data: {
          nodes: [
            {
              id: "agent:root",
              name: "root",
              type: "agent",
              span_count: 1,
              avg_latency_ms: 10,
              total_tokens: 2,
              total_cost: 0,
              error_count: 0,
              trace_count: 1,
            },
          ],
          edges: [],
          path_edges: [],
        },
        isLoading: false,
        isError: false,
        pollingPaused: false,
      };
      const { container } = renderView();
      await waitFor(() =>
        expect(
          container.querySelectorAll(
            viewMode === "agentGraph"
              ? ".react-flow"
              : "svg:has(.agent-path-node)",
          ),
        ).toHaveLength(2),
      );
      expect(
        screen.queryByText(/Please retry in a moment/),
      ).not.toBeInTheDocument();
    });
  },
);
