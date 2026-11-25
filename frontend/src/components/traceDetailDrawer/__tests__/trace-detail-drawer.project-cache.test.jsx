import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import SelectedNodeContext from "../selectedNodeContext";

const mocks = vi.hoisted(() => ({ params: {} }));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => mocks.params,
}));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(() => Promise.resolve({ data: { result: {} } })),
  endpoints: {
    project: {
      getAnnotationLabels: () => "/model-hub/annotations-labels/",
      addAnnotationValuesForSpan: () => "/tracer/add-annotation-values/",
      getTrace: (id) => `/tracer/trace/${id}/`,
      getTraceIdByIndex: () => "/tracer/trace/get_trace_id_by_index/",
      getTraceIdByIndexObserve: () => "/tracer/trace/observe-index/",
      getTraceIdByIndexSpansAsBase: () => "/tracer/span/index/",
      getTraceIdByIndexSpansAsObserve: () => "/tracer/span/observe-index/",
      getObservationSpan: (id) => `/tracer/observation-span/${id}/`,
    },
  },
}));

vi.mock("src/utils/Mixpanel", () => ({
  Events: {},
  PropertyName: {},
  trackEvent: vi.fn(),
}));

vi.mock("../common", () => ({
  useTraceErrorAnalysis: () => ({ data: undefined, isPending: true }),
}));

vi.mock("../drawer-bottom", () => ({
  default: ({ observationSpan }) => (
    <div data-testid="drawer-bottom">{observationSpan?.name ?? ""}</div>
  ),
}));
vi.mock("../trace-tree", () => ({ default: () => null }));
vi.mock("../drawer-right", () => ({ default: () => null }));
vi.mock("../ErrorAnalysis", () => ({ default: () => null }));
vi.mock("../addToDataset/add-dataset", () => ({ default: () => null }));
vi.mock("../AnnotateDrawer", () => ({ default: () => null }));
vi.mock("../AnnotationSidebarContent", () => ({ default: () => null }));
vi.mock("../add-annotations-drawer", () => ({ default: () => null }));
vi.mock("../AddLabelDrawer", () => ({ default: () => null }));

import TraceDetailDrawer from "../trace-detail-drawer";

const SPAN_ID = "span-x";
const spanUrl = `/tracer/observation-span/${SPAN_ID}/`;

// The app's QueryClient defaults (src/app.jsx): data stays fresh for 5 s, so
// a remount inside that window is served from cache without a request.
const createQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 5 * 1000 } },
  });

function Harness({ queryClient, viewOptions }) {
  return (
    <QueryClientProvider client={queryClient}>
      <SelectedNodeContext.Provider
        value={{ selectedNode: { id: SPAN_ID }, setSelectedNode: () => {} }}
      >
        <TraceDetailDrawer
          open
          onClose={() => {}}
          traceData={{ trace_id: "trace-1" }}
          viewOptions={viewOptions}
        />
      </SelectedNodeContext.Provider>
    </QueryClientProvider>
  );
}

Harness.propTypes = {
  queryClient: PropTypes.object.isRequired,
  viewOptions: PropTypes.object,
};

const spanReadProjects = () =>
  axios.get.mock.calls
    .filter(([url]) => url === spanUrl)
    .map(([, config]) => config?.params?.project_id);

// The same span id can exist in several projects (replays, re-imports). The
// drawer pins the span read to the route's project, so the cached span must
// be keyed by that project too, or reopening span X under project B serves
// project A's copy from cache.
describe("TraceDetailDrawer span read cache is per project", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.get.mockImplementation((url, config) => {
      if (url === spanUrl) {
        return Promise.resolve({
          data: { result: { name: `copy of ${config?.params?.project_id}` } },
        });
      }
      if (url === "/model-hub/annotations-labels/") {
        return Promise.resolve({ data: { results: [] } });
      }
      return Promise.resolve({ data: { result: { observation_spans: [] } } });
    });
  });

  it.each([
    ["observationSpan", undefined],
    ["observationSpan-loading", { showEvalLoadingStates: true }],
  ])(
    "reads project B's copy after project A's (%s)",
    async (_key, viewOptions) => {
      const queryClient = createQueryClient();

      mocks.params = { projectId: "project-a", runId: "run-a" };
      const first = render(
        <Harness queryClient={queryClient} viewOptions={viewOptions} />,
      );
      expect(await screen.findByText("copy of project-a")).toBeInTheDocument();
      first.unmount();

      mocks.params = { projectId: "project-b", runId: "run-b" };
      render(<Harness queryClient={queryClient} viewOptions={viewOptions} />);

      expect(await screen.findByText("copy of project-b")).toBeInTheDocument();
      expect(screen.queryByText("copy of project-a")).not.toBeInTheDocument();
      expect(spanReadProjects()).toEqual(["project-a", "project-b"]);
    },
  );
});
