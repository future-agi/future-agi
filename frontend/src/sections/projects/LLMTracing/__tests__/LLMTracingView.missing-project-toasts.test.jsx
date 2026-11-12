/* eslint-disable react/prop-types */
import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handleError } from "src/utils/queryErrorHandler";
import { useGetProjectDetails } from "src/api/project/project-detail";
import { useGetSavedViews } from "src/api/project/saved-views";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import LLMTracingView from "../LLMTracingView";

// An Observe page for a missing project (/dashboard/observe/<missing
// id>/llm-tracing) says "This project could not be loaded" once, on the
// page, with no app-wide toast for the project read or the saved-views read
// on top of it. The real LLMTracingView, the real project
// and saved-views hooks and the app's QueryCache handler
// (utils/queryErrorHandler, unchanged) run here; only HTTP, the toast call
// and unrelated panels are replaced.

const PROJECT_ID = "project-1";
const PROJECT_READ = ["project-detail", PROJECT_ID];
const VIEWS_READ = ["saved-views", PROJECT_ID];

// The EmptyContent icon of the in-page state.
registerOfflineIcon(
  "solar:danger-triangle-linear",
  bundled["solar:danger-triangle-linear"],
);

// What the axios interceptor rejects with for these backend answers.
const PROJECT_NOT_FOUND = {
  status: false,
  code: "bad_request",
  message: "Project Not Found",
  result: "Project Not Found",
  statusCode: 400,
};
const SAVED_VIEWS_PROJECT_NOT_FOUND = {
  status: false,
  code: "not_found",
  message: "Project not found.",
  result: "Project not found.",
  statusCode: 404,
};
const SAVED_VIEWS_FAILED = {
  status: false,
  code: "bad_request",
  message: "Failed to list saved views.",
  result: "Failed to list saved views.",
  statusCode: 400,
};
const OTHER_READ_FAILED = {
  status: false,
  code: "bad_request",
  message: "Could not load the run summary.",
  result: "Could not load the run summary.",
  statusCode: 400,
};
const GATEWAY_CONFIG_FAILED = {
  status: false,
  code: "bad_request",
  message: "Could not load the gateway config.",
  result: "Could not load the gateway config.",
  statusCode: 400,
};
const PROJECT_LOADED = {
  data: { status: true, result: { source: "observe" } },
};

const harness = vi.hoisted(() => ({
  toast: vi.fn(),
  answers: { projectDetail: null, savedViews: null },
  rerenderShell: null,
  attributes: [],
  dashboardFilterValues: [],
  emptyFilters: [],
  inventoryControlProps: {},
  observeHeader: {
    activeViewConfig: null,
    registerGetViewConfig: vi.fn(),
    setActiveViewConfig: vi.fn(),
    setHeaderConfig: vi.fn(),
  },
  navigate: vi.fn(),
  traceGrid: {},
  spanGrid: {},
  replayState: {
    openReplaySessionDrawer: {},
    setIsReplayDrawerCollapsed: vi.fn(),
    setCreatedReplay: vi.fn(),
    setReplayType: vi.fn(),
    setOpenReplaySessionDrawer: vi.fn(),
  },
  testDetailState: { setTestDetailDrawerOpen: vi.fn() },
}));

const emptyGridState = () => ({
  toggledNodes: [],
  selectAll: false,
  totalRowCount: 0,
  totalRowCountLowerBound: null,
  totalRowCountIsLowerBound: false,
});

// A request the test answers later. The no-op catch keeps a rejection from
// counting as unhandled before a read is waiting on it.
const deferred = () => {
  const answer = {};
  answer.promise = new Promise((resolve, reject) => {
    answer.resolve = resolve;
    answer.reject = reject;
  });
  answer.promise.catch(() => {});
  return answer;
};

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: harness.toast,
}));

// The project and saved-views reads answer from `harness.answers` (a
// function returning the promise); every other read stays pending.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  const detailUrl =
    actual.endpoints.project.projectExperimentDetail("project-1");
  const savedViewsUrl = actual.endpoints.savedViews.list;
  return {
    ...actual,
    default: {
      get: vi.fn((url) => {
        if (url === detailUrl && harness.answers.projectDetail)
          return harness.answers.projectDetail();
        if (url === savedViewsUrl && harness.answers.savedViews)
          return harness.answers.savedViews();
        return new Promise(() => {});
      }),
      post: vi.fn(() => new Promise(() => {})),
    },
    readQuery: vi.fn(() => new Promise(() => {})),
  };
});

vi.mock("src/auth/hooks", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuthContext: () => ({ role: "Admin" }),
}));

vi.mock("react-helmet-async", async (importOriginal) => ({
  ...(await importOriginal()),
  Helmet: () => null,
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useNavigate: () => harness.navigate,
  useParams: () => ({ observeId: "project-1" }),
}));

vi.mock("src/routes/hooks/use-url-state", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    useUrlState: (key, defaultValue) =>
      ReactModule.useState(key === "selectedTab" ? "trace" : defaultValue),
  };
});

vi.mock(
  "src/sections/project/context/ObserveHeaderContext",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useObserveHeader: () => harness.observeHeader,
  }),
);

vi.mock("../useLLMTracingFilters", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    useLLMTracingFilters: (defaultFilters, defaultDateFilter) => {
      const [filters, setFilters] = ReactModule.useState(defaultFilters);
      const [dateFilter, setDateFilter] =
        ReactModule.useState(defaultDateFilter);
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

vi.mock("../states", async (importOriginal) => {
  const llmState = {
    resetStates: vi.fn(),
    viewMode: "graph",
    setViewMode: vi.fn(),
  };
  return {
    ...(await importOriginal()),
    resetSpanGridStore: vi.fn(),
    resetTraceGridStore: vi.fn(),
    useLLMTracingStoreShallow: (selector) => selector(llmState),
    useTraceGridStoreShallow: (selector) => selector(harness.traceGrid),
    useSpanGridStoreShallow: (selector) => selector(harness.spanGrid),
  };
});

vi.mock("../TraceGrid", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    default: ReactModule.forwardRef(function TraceGridDouble(props, _ref) {
      return <div data-testid={`${props.compareType}-trace-grid`} />;
    }),
  };
});

vi.mock("../SpanGrid", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    default: ReactModule.forwardRef(function SpanGridDouble(props, _ref) {
      return <div data-testid={`${props.compareType}-span-grid`} />;
    }),
  };
});

vi.mock("src/sections/agents/CallLogs/CallLogsGrid", async (importOriginal) => {
  const ReactModule = await import("react");
  return {
    ...(await importOriginal()),
    default: ReactModule.forwardRef(function CallLogsGridDouble() {
      return null;
    }),
  };
});

vi.mock("../ObserveToolbar", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("../SelectAllBanner", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock(
  "src/api/annotation-queues/annotation-queues",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useAnnotationQueuesList: () => ({
      data: { results: [] },
      isLoading: false,
    }),
    useAddQueueItems: () => ({ mutate: vi.fn(), isPending: false }),
  }),
);

vi.mock(
  "src/sections/annotations/queues/create-queue-drawer",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

vi.mock("src/sections/test-detail/states", async (importOriginal) => ({
  ...(await importOriginal()),
  useTestDetailSideDrawerStoreShallow: (selector) =>
    selector(harness.testDetailState),
}));

vi.mock(
  "src/sections/projects/UsersView/useProjectFilterField",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: () => ({ data: harness.dashboardFilterValues }),
}));

vi.mock("../useCursorAttributeInventory", async (importOriginal) => ({
  ...(await importOriginal()),
  useCursorAttributeInventory: () => ({
    attributes: harness.attributes,
    inventoryControlProps: harness.inventoryControlProps,
  }),
}));

vi.mock("src/contexts/WorkspaceContext", async (importOriginal) => ({
  ...(await importOriginal()),
  useWorkspace: () => ({ currentWorkspaceId: "workspace-1" }),
}));

vi.mock("src/api/project/agent-graph", async (importOriginal) => ({
  ...(await importOriginal()),
  useAgentGraph: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    pollingPaused: false,
  }),
}));

vi.mock(
  "src/sections/projects/SessionsView/ReplaySessions/store",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useReplaySessionsStoreShallow: (selector) => selector(harness.replayState),
    useSessionsGridStore: { getState: () => ({ setToggledNodes: vi.fn() }) },
  }),
);

vi.mock("src/api/project/replay-sessions", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateReplaySessions: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The saved-views read stays real; only its writes are replaced.
vi.mock("src/api/project/saved-views", async (importOriginal) => ({
  ...(await importOriginal()),
  useCreateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateSavedView: () => ({ mutate: vi.fn() }),
  useUpdateWorkspaceSavedView: () => ({ mutate: vi.fn() }),
}));

vi.mock("../GraphSection/PrimaryGraph", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("../GraphSection/AgentGraph", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("../GraphSection/AgentPath", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("../FilterChips", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("../TracingControls", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock("../CustomColumnDialog", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));

vi.mock(
  "src/components/custom-datepicker/DatePicker",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

vi.mock("src/components/tooltip", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ children }) => children,
}));

vi.mock(
  "src/components/traceDetail/AddTagsPopover",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

vi.mock(
  "src/components/traceDetailDrawer/addToDataset/add-dataset",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

vi.mock(
  "src/components/traceDetailDrawer/AnnotateDrawer",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

vi.mock(
  "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);

// ObservePage, the route shell, reads the same project and saved views with
// no options of its own, as it does in the app (ObserveHeader and the tab
// bar read them the same way). `harness.rerenderShell` re-renders it alone,
// as a header or tab-bar update does on the route, so its options become
// the reads' own. It sits inside the Suspense boundary so it mounts in the
// same commit as the view.
function ObservePageReads({ children }) {
  const [, setRenders] = React.useState(0);
  React.useEffect(() => {
    harness.rerenderShell = () => setRenders((count) => count + 1);
  }, []);
  useGetProjectDetails(PROJECT_ID);
  useGetSavedViews(PROJECT_ID);
  return children;
}

// Some other read on the same page, with no options.
function OtherRead() {
  useQuery({
    queryKey: ["c8-other-read", PROJECT_ID],
    queryFn: () => Promise.reject(OTHER_READ_FAILED),
  });
  return null;
}

async function renderView({ withOtherRead = false } = {}) {
  const queryClient = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    render(
      <QueryClientProvider client={queryClient}>
        <React.Suspense fallback={<div>Loading tracing view</div>}>
          <ObservePageReads>
            <LLMTracingView />
            {withOtherRead && <OtherRead />}
          </ObservePageReads>
        </React.Suspense>
      </QueryClientProvider>,
    );
  });
  return queryClient;
}

const readOf = (queryClient, queryKey) =>
  queryClient.getQueryCache().find({ queryKey });

const readStatus = (queryClient, queryKey) =>
  queryClient.getQueryState(queryKey)?.status;

const toastMessages = () =>
  harness.toast.mock.calls.map(([message]) => message);

beforeEach(() => {
  harness.toast.mockReset();
  harness.navigate.mockReset();
  harness.answers = { projectDetail: null, savedViews: null };
  harness.rerenderShell = null;
  harness.traceGrid = emptyGridState();
  harness.spanGrid = emptyGridState();
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("Observe page for a missing project", () => {
  it("says it once, on the page, even when a watcher with no options rendered last", async () => {
    const projectAnswer = deferred();
    const viewsAnswer = deferred();
    harness.answers = {
      projectDetail: () => projectAnswer.promise,
      savedViews: () => viewsAnswer.promise,
    };
    const queryClient = await renderView();
    // The shell and the tracing view both watch both reads.
    await waitFor(() => {
      expect(
        readOf(queryClient, PROJECT_READ)?.getObserversCount(),
      ).toBeGreaterThanOrEqual(2);
      expect(
        readOf(queryClient, VIEWS_READ)?.getObserversCount(),
      ).toBeGreaterThanOrEqual(2);
    });

    // The shell renders after the view, so each read's own options are the
    // shell's, which name no in-page state. An option set only at the
    // view's call site would lose here.
    act(() => harness.rerenderShell());
    expect(
      readOf(queryClient, PROJECT_READ).options.meta?.showsFailure,
    ).toBeUndefined();
    expect(
      readOf(queryClient, VIEWS_READ).options.meta?.showsFailure,
    ).toBeUndefined();

    await act(async () => {
      projectAnswer.reject(PROJECT_NOT_FOUND);
      viewsAnswer.reject(SAVED_VIEWS_PROJECT_NOT_FOUND);
    });

    expect(
      await screen.findByText("This project could not be loaded"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "It may have been deleted, or you may not have access to it.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Back to projects" }),
    ).toBeInTheDocument();
    expect(readStatus(queryClient, PROJECT_READ)).toBe("error");
    expect(readStatus(queryClient, VIEWS_READ)).toBe("error");
    // Before the fix both reads were toasted here: "Project Not Found" and
    // "Project not found.".
    expect(toastMessages()).toEqual([]);
  });

  it("still toasts a failed refetch of a project that had loaded", async () => {
    harness.answers = {
      projectDetail: () => Promise.resolve(PROJECT_LOADED),
      savedViews: null,
    };
    const queryClient = await renderView();
    await waitFor(() =>
      expect(readStatus(queryClient, PROJECT_READ)).toBe("success"),
    );

    harness.answers.projectDetail = () => Promise.reject(PROJECT_NOT_FOUND);
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: PROJECT_READ });
    });

    await waitFor(() =>
      expect(harness.toast).toHaveBeenCalledWith("Project Not Found", {
        variant: "error",
      }),
    );
    // The loaded project stays on screen; no in-page state replaces it.
    expect(
      screen.queryByText("This project could not be loaded"),
    ).not.toBeInTheDocument();
  });

  it("still toasts a saved-views 404 once the project has loaded", async () => {
    const viewsAnswer = deferred();
    harness.answers = {
      projectDetail: () => Promise.resolve(PROJECT_LOADED),
      savedViews: () => viewsAnswer.promise,
    };
    const queryClient = await renderView();
    await waitFor(() =>
      expect(readStatus(queryClient, PROJECT_READ)).toBe("success"),
    );

    await act(async () => {
      viewsAnswer.reject(SAVED_VIEWS_PROJECT_NOT_FOUND);
    });

    await waitFor(() =>
      expect(harness.toast).toHaveBeenCalledWith("Project not found.", {
        variant: "error",
      }),
    );
    // Nothing on the page says it: the project loaded.
    expect(
      screen.queryByText("This project could not be loaded"),
    ).not.toBeInTheDocument();
  });

  it("still toasts a saved-views failure of a loaded project", async () => {
    harness.answers = {
      projectDetail: () => Promise.resolve(PROJECT_LOADED),
      savedViews: () => Promise.reject(SAVED_VIEWS_FAILED),
    };
    await renderView();

    await waitFor(() =>
      expect(harness.toast).toHaveBeenCalledWith(
        "Failed to list saved views.",
        { variant: "error" },
      ),
    );
  });

  it("still toasts a different read that fails on the missing project's page", async () => {
    harness.answers = {
      projectDetail: () => Promise.reject(PROJECT_NOT_FOUND),
      savedViews: () => Promise.reject(SAVED_VIEWS_PROJECT_NOT_FOUND),
    };
    await renderView({ withOtherRead: true });

    expect(
      await screen.findByText("This project could not be loaded"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(harness.toast).toHaveBeenCalledWith(
        "Could not load the run summary.",
        { variant: "error" },
      ),
    );
  });
});

describe("the app-wide toast handler (utils/queryErrorHandler)", () => {
  it("still reads only a failed read's own options, not every watcher's", () => {
    // A shared read where one watcher sets errorHandled and shows nothing
    // (Guardrails' gateway config) and another sets nothing and rendered
    // last (Rules): Rules' failure still toasts.
    const gatewayConfigRead = {
      options: { meta: undefined },
      observers: [
        { options: { meta: { errorHandled: true } } },
        { options: { meta: undefined } },
      ],
      state: { data: undefined, error: GATEWAY_CONFIG_FAILED },
    };

    handleError(GATEWAY_CONFIG_FAILED, gatewayConfigRead);

    expect(harness.toast).toHaveBeenCalledWith(
      "Could not load the gateway config.",
      { variant: "error" },
    );
  });
});
