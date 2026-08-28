import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { useForm } from "react-hook-form";
import { ErrorBoundary } from "react-error-boundary";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { INVALID_MAPPING_LABEL } from "src/sections/evals/utils/evalMappingPath";
import TaskLivePreview from "../TaskLivePreview";

const axiosGetMock = vi.hoisted(() => vi.fn());

vi.mock("src/utils/axios", () => ({
  default: { get: axiosGetMock },
  endpoints: {
    project: {
      getSpansForObserveProject: () => "/tracer/observe-project-spans/",
      getTracesForObserveProject: () => "/tracer/observe-project-traces/",
      projectSessionList: () => "/tracer/project-session-list/",
      getTrace: (id) => `/tracer/trace/${id}/`,
      traceSession: "/tracer/trace-session/",
      getCallLogs: "/tracer/call-logs/",
      getVoiceCallDetail: "/tracer/voice-call-detail/",
    },
  },
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="icon">{icon}</span>,
}));

vi.mock("src/sections/evals/components/DatasetTestMode", () => ({
  JsonValueTree: () => <span>json value</span>,
}));

vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({
  default: () => <div>eval result</div>,
}));

vi.mock("src/sections/evals/components/SpanRowList", () => ({
  default: () => <div>span rows</div>,
}));

const renderPreview = (evalsDetails) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  const Harness = () => {
    const { control } = useForm({
      defaultValues: {
        filters: [],
        startDate: null,
        endDate: null,
        rowType: "spans",
        evalsDetails,
      },
    });
    return <TaskLivePreview control={control} projectId="project-1" />;
  };

  return render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
};

// The saved value that crashed production was an object. Its inner shape was
// never recoverable from the report, so these tests assert on the value being a
// non-string — which is the whole gate — and never on a particular inner shape.
const OBJECT_MAPPING_VALUE = { value: "output.value" };

describe("TaskLivePreview — variable mapping", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({
      data: {
        result: {
          table: [{ span_id: "span-1", output: { value: "hi" } }],
          metadata: { total_rows: 1 },
          config: [],
        },
      },
    });
  });

  it("renders a mapping whose value is an object instead of tearing down the page", async () => {
    renderPreview([
      {
        id: "eval-1",
        name: "Groundedness",
        mapping: { context: OBJECT_MAPPING_VALUE, answer: "output.value" },
      },
    ]);

    expect(await screen.findByText("Variable Mapping")).toBeInTheDocument();
    expect(screen.getByText("context")).toBeInTheDocument();
    expect(screen.getByText("answer")).toBeInTheDocument();
    expect(screen.getByText(INVALID_MAPPING_LABEL)).toBeInTheDocument();
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });

  it("flags the object value as invalid while the path value stays resolved", async () => {
    renderPreview([
      {
        id: "eval-1",
        name: "Groundedness",
        mapping: {
          context: OBJECT_MAPPING_VALUE,
          answer: "output.value",
          absent: "nope.not.here",
        },
      },
    ]);

    // Every hit is forced to "unknown" until spanDetail resolves, which also
    // suppresses "(not in row)". Awaiting it therefore pins the assertions
    // below to the post-resolution render — asserting its absence would have
    // passed mid-fetch and proved nothing about resolution.
    expect(await screen.findByText("(not in row)")).toBeInTheDocument();
    expect(screen.getByText("output.value")).toBeInTheDocument();
    expect(screen.getByText(INVALID_MAPPING_LABEL)).toBeInTheDocument();
    // Only the absent path warns, so output.value genuinely resolved.
    expect(screen.getAllByText("(not in row)")).toHaveLength(1);
  });
});

describe("TaskLivePreview — app error boundary", () => {
  beforeEach(() => {
    axiosGetMock.mockReset();
    axiosGetMock.mockResolvedValue({
      data: {
        result: {
          table: [{ span_id: "span-1", output: { value: "hi" } }],
          metadata: { total_rows: 1 },
          config: [],
        },
      },
    });
  });

  it("does not trip the boundary that wraps the whole app", async () => {
    // app.jsx wraps <Router /> in this same react-error-boundary with no reset
    // on navigation, so anything thrown while rendering a mapping takes the
    // whole application down until a reload — that was the reported symptom.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const Harness = () => {
      const { control } = useForm({
        defaultValues: {
          filters: [],
          startDate: null,
          endDate: null,
          rowType: "spans",
          evalsDetails: [
            {
              id: "eval-1",
              name: "Groundedness",
              mapping: { context: OBJECT_MAPPING_VALUE },
            },
          ],
        },
      });
      return <TaskLivePreview control={control} projectId="project-1" />;
    };

    render(
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary
          FallbackComponent={() => <div>application error boundary</div>}
        >
          <Harness />
        </ErrorBoundary>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Variable Mapping")).toBeInTheDocument();
    expect(
      screen.queryByText("application error boundary"),
    ).not.toBeInTheDocument();
  });
});
