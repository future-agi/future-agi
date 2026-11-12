import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import {
  OBSERVE_KNOWN_ISSUES_URL,
  VOICE_OBSERVE_NOTICE,
} from "src/utils/platformObserveAvailability";

// OBS-SW-110: a new voice project's empty call list must say how calls
// arrive, whether the provider refused the last fetch, repeat the release
// notice and point to the agent settings where the key is set.

const {
  providerListState,
  queryClientMock,
  simulationAgentsState,
  useCallLogsMock,
  useQueryMock,
} = vi.hoisted(() => ({
  providerListState: { response: undefined },
  queryClientMock: { prefetchQuery: vi.fn() },
  simulationAgentsState: { data: [] },
  useCallLogsMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock("ag-grid-react", async () => {
  const ReactModule = await import("react");
  const AgGridReact = ReactModule.forwardRef(
    function MockAgGridReact(props, _ref) {
      // eslint-disable-next-line react/prop-types
      const { rowData, noRowsOverlayComponent } = props;
      return (
        <div data-testid="call-logs-grid">
          {rowData?.length === 0 && noRowsOverlayComponent?.()}
        </div>
      );
    },
  );
  return { AgGridReact };
});

vi.mock("src/styles/clean-data-table.css", () => ({}));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal()),
  useQuery: (...args) => useQueryMock(...args),
  useQueryClient: () => queryClientMock,
}));
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => ({}),
}));
vi.mock("src/sections/agents/helper", () => ({
  getCallLogsColumnDefs: () => [],
  prefetchCallLogs: vi.fn(),
  useCallLogs: (...args) => useCallLogsMock(...args),
}));
vi.mock("src/sections/agents/store/agentDetailsStore", () => ({
  useAgentDetailsStore: () => ({ selectedVersion: "version-1" }),
}));
vi.mock("src/sections/agents/store", () => ({
  useShallowToggleAnnotationsStore: (selector) =>
    selector({ showMetricsIds: false, reset: vi.fn() }),
}));
vi.mock("src/sections/test-detail/states", () => ({
  resetState: vi.fn(),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector({ testDetailDrawerOpen: null }),
}));
vi.mock(
  "src/sections/test-detail/TestDetailDrawer/TestDetailSideDrawer",
  () => ({ default: () => null }),
);
vi.mock("src/components/show", () => ({
  // eslint-disable-next-line react/prop-types
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useSimulationAgents: () => simulationAgentsState,
}));

import CallLogsGrid from "../CallLogsGrid";

const FETCH_COPY =
  "Calls from your connected provider are fetched about every 10 minutes.";
const KEY_REJECTED_COPY =
  "Last fetch failed: the provider rejected the API key.";
const SETTINGS_LINK =
  "Check the API key and assistant ID in your agent's settings";
const KNOWN_ISSUES_LINK = "2.0.0 release notes: Known issues";

const emptyProjectRead = {
  data: {
    count: 0,
    count_is_lower_bound: false,
    total_pages: 1,
    current_page: 1,
    results: [],
    config: [],
    has_more: false,
    next_cursor: null,
    query_complete: true,
    query_status: "complete",
    query_error_code: null,
  },
  isLoading: false,
  error: null,
  queryKey: ["callLogs", "project", "project-1", 25, {}, 1, 0],
};

// GET /tracer/observability-provider/?project_id=... as axios returns it:
// the success envelope wraps { metadata, providers }.
const providerList = (...providers) => ({
  data: {
    status: true,
    result: {
      metadata: {
        total_count: providers.length,
        current_page: 0,
        page_size: 20,
        total_pages: 1,
        next_page: null,
      },
      providers,
    },
  },
});

const provider = (overrides = {}) => ({
  id: "provider-1",
  project: "project-1",
  provider: "vapi",
  enabled: true,
  metadata: {},
  ...overrides,
});

const providerQueryCalls = () =>
  useQueryMock.mock.calls
    .map(([options]) => options)
    .filter((options) => options?.queryKey?.[0] === "observabilityProvider");

const renderProject = () =>
  render(<CallLogsGrid id="project-1" module="project" hideDrawer />);

describe("CallLogsGrid empty voice project", () => {
  beforeEach(() => {
    providerListState.response = undefined;
    simulationAgentsState.data = [];
    useCallLogsMock.mockReset().mockReturnValue(emptyProjectRead);
    useQueryMock.mockReset().mockImplementation((options) => {
      if (options?.queryKey?.[0] !== "observabilityProvider") {
        return { data: undefined };
      }
      const response = options.enabled ? providerListState.response : undefined;
      return {
        data:
          response && typeof options.select === "function"
            ? options.select(response)
            : undefined,
      };
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("says how a connected provider's calls arrive and where to check the key", () => {
    providerListState.response = providerList(provider());
    simulationAgentsState.data = [
      { id: "agent-other", observability_project_id: "project-2" },
      { id: "agent-1", observability_project_id: "project-1" },
    ];

    renderProject();

    expect(screen.getByText("No calls found")).toBeInTheDocument();
    expect(screen.getByText(FETCH_COPY)).toBeInTheDocument();
    expect(screen.getByText(VOICE_OBSERVE_NOTICE)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: KNOWN_ISSUES_LINK }),
    ).toHaveAttribute("href", OBSERVE_KNOWN_ISSUES_URL);
    expect(screen.getByRole("link", { name: SETTINGS_LINK })).toHaveAttribute(
      "href",
      "/dashboard/agents/connected/agent-1/definition",
    );
    expect(screen.queryByText(KEY_REJECTED_COPY)).not.toBeInTheDocument();
  });

  it("says the provider rejected the API key after a refused fetch", () => {
    providerListState.response = providerList(
      provider({
        provider: "retell",
        last_fetch_error: {
          status_code: 401,
          at: "2026-11-09T12:00:00+00:00",
        },
      }),
    );
    simulationAgentsState.data = [
      { id: "agent-1", observability_project_id: "project-1" },
    ];

    renderProject();

    expect(screen.getByText(KEY_REJECTED_COPY)).toBeInTheDocument();
    expect(screen.getByText(FETCH_COPY)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: SETTINGS_LINK })).toHaveAttribute(
      "href",
      "/dashboard/agents/connected/agent-1/definition",
    );
  });

  it("links to Connected agents when no agent owns the project", () => {
    providerListState.response = providerList(provider());

    renderProject();

    expect(screen.getByRole("link", { name: SETTINGS_LINK })).toHaveAttribute(
      "href",
      "/dashboard/agents/connected",
    );
  });

  it("reads the project's provider for a settled empty project read", async () => {
    const get = vi
      .spyOn(axios, "get")
      .mockResolvedValue(providerList(provider()));

    renderProject();

    const [options] = providerQueryCalls();
    expect(options).toEqual(
      expect.objectContaining({
        queryKey: ["observabilityProvider", "project-1"],
        enabled: true,
      }),
    );
    await options.queryFn();
    expect(get).toHaveBeenCalledWith("/tracer/observability-provider/", {
      params: { project_id: "project-1" },
    });
  });

  it("polls the provider status while the project stays empty", () => {
    // OBS-SW-171: a key the provider rejects after the page opened shows up
    // without a refocus or reload.
    providerListState.response = providerList(provider());

    renderProject();

    const [options] = providerQueryCalls();
    expect(options).toEqual(
      expect.objectContaining({
        queryKey: ["observabilityProvider", "project-1"],
        refetchInterval: 60 * 1000,
      }),
    );
  });

  it("keeps the bare line for a provider that is not fetched on a schedule", () => {
    providerListState.response = providerList(
      provider({ provider: "livekit" }),
    );

    renderProject();

    expect(screen.getByText("No calls found")).toBeInTheDocument();
    expect(screen.queryByText(FETCH_COPY)).not.toBeInTheDocument();
    expect(screen.queryByText(VOICE_OBSERVE_NOTICE)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: SETTINGS_LINK }),
    ).not.toBeInTheDocument();
  });

  it("does not read the provider while the project has calls or for other grids", () => {
    useCallLogsMock.mockReturnValue({
      ...emptyProjectRead,
      data: {
        ...emptyProjectRead.data,
        count: 1,
        results: [{ id: "call-1", trace_id: "call-1", status: "completed" }],
      },
    });
    const view = renderProject();
    view.unmount();

    useCallLogsMock.mockReturnValue({
      ...emptyProjectRead,
      queryKey: ["callLogs", "simulate", "agent-1", "version-1", 25, {}, 1],
    });
    render(<CallLogsGrid id="agent-1" module="simulate" hideDrawer />);
    expect(screen.getByText("No calls found")).toBeInTheDocument();

    expect(providerQueryCalls()).toEqual([]);
  });

  it("keeps the errors-only empty line", () => {
    providerListState.response = providerList(provider());

    render(
      <CallLogsGrid id="project-1" module="project" hideDrawer showErrors />,
    );

    expect(screen.getByText("No error found")).toBeInTheDocument();
    expect(screen.queryByText(FETCH_COPY)).not.toBeInTheDocument();
  });
});
