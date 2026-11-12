import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import { useErrorFeedOverview } from "src/api/errorFeed/error-feed";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useErrorFeedStore } from "../../store";
import { TRACE_STATUS } from "../../constants";
import OverviewTab, { TraceGraphView } from "../OverviewTab";

// W10-ERROR-FEED-U09: Trace Evidence offers "Split with working" only when
// the issue has a passing trace to compare against, never promises one with
// backend jargon, and pairs every span load error with a Retry action.

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedOverview: vi.fn(),
}));
vi.mock("src/api/project/trace-detail", () => ({
  useGetTraceDetail: vi.fn(),
}));
vi.mock("src/api/shared-links", () => ({
  useSharedIssueTrace: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentGraph", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentPath", () => ({
  default: () => null,
}));

const FAIL_TRACE_ID = "synthetic-split-fail-trace";
const PASS_TRACE_ID = "synthetic-split-pass-trace";
const NO_PASSING_REASON = "No passing trace found for this issue yet";

const FAIL_REEL = [{ label: "USER INPUT", text: "Synthetic failing input" }];
const PASS_REEL = [{ label: "USER INPUT", text: "Synthetic passing input" }];

function mockOverview({ passReel = [] } = {}) {
  useErrorFeedOverview.mockReturnValue({
    isLoading: false,
    data: {
      events_over_time: [],
      pattern_summary: null,
      representative_total: 1,
      representative_traces: [
        {
          id: FAIL_TRACE_ID,
          timestamp: "2000-01-01T00:00:00Z",
          status: TRACE_STATUS.FAIL,
          summary: { input_tokens: 0, output_tokens: 0, latency_ms: 1 },
          evidence: { fail_reel: FAIL_REEL, pass_reel: passReel },
        },
      ],
    },
  });
}

function scannerIssue({ workingTraceId } = {}) {
  return {
    cluster_id: "synthetic-split-cluster",
    project_id: "synthetic-split-project",
    source: "scanner",
    ...(workingTraceId ? { success_trace: { trace_id: workingTraceId } } : {}),
  };
}

const splitButton = () =>
  screen.getByRole("button", { name: "Split with working" });

beforeEach(() => {
  vi.clearAllMocks();
  useErrorFeedStore.setState({ selectedTraceIdByCluster: {} });
  useGetTraceDetail.mockReturnValue({
    data: { observation_spans: [] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

describe("Trace Evidence split with working", () => {
  it("shows the split unavailable with the reason when the issue has no passing trace", async () => {
    const user = userEvent.setup();
    mockOverview();
    render(<OverviewTab _error={scannerIssue()} />);

    expect(splitButton()).toHaveAttribute("aria-disabled", "true");
    await user.hover(splitButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      NO_PASSING_REASON,
    );
  });

  it("keeps the Agent Graph in place when the split is unavailable", async () => {
    const user = userEvent.setup();
    mockOverview();
    render(<OverviewTab _error={scannerIssue()} />);

    await user.click(screen.getByRole("tab", { name: /Agent Graph/ }));
    expect(splitButton()).toHaveAttribute("aria-disabled", "true");
    await user.click(splitButton());

    expect(screen.queryByText(/KNN matching/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Single view" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("No span data available for this trace"),
    ).toBeInTheDocument();
  });

  it("drops a breadcrumb split when switching to a graph view with no working trace", async () => {
    const user = userEvent.setup();
    mockOverview({ passReel: PASS_REEL });
    render(<OverviewTab _error={scannerIssue()} />);

    await user.click(splitButton());
    expect(screen.getByText("Synthetic passing input")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Agent Path/ }));

    expect(screen.queryByText(/KNN matching/)).not.toBeInTheDocument();
    expect(splitButton()).toHaveAttribute("aria-disabled", "true");
    expect(
      screen.getByText("No span data available for this trace"),
    ).toBeInTheDocument();
  });

  it("splits the Agent Graph with the working trace when one exists", async () => {
    const user = userEvent.setup();
    mockOverview({ passReel: PASS_REEL });
    render(
      <OverviewTab _error={scannerIssue({ workingTraceId: PASS_TRACE_ID })} />,
    );

    await user.click(screen.getByRole("tab", { name: /Agent Graph/ }));
    expect(splitButton()).not.toHaveAttribute("aria-disabled");
    await user.click(splitButton());

    expect(screen.getByText("Working trace")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Single view" }),
    ).toBeInTheDocument();
  });
});

describe("Trace span load errors", () => {
  it("offers Retry beside the single-trace load error", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    useGetTraceDetail.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    });
    render(<TraceGraphView traceId={FAIL_TRACE_ID} mode="graph" />);

    expect(
      screen.getByText("Could not load trace spans. Please retry."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("offers Retry for the side of the split that failed to load", async () => {
    const user = userEvent.setup();
    const failRefetch = vi.fn();
    const passRefetch = vi.fn();
    useGetTraceDetail.mockImplementation((traceId) =>
      traceId === PASS_TRACE_ID
        ? {
            data: undefined,
            isLoading: false,
            isError: true,
            refetch: passRefetch,
          }
        : {
            data: { observation_spans: [] },
            isLoading: false,
            isError: false,
            refetch: failRefetch,
          },
    );
    mockOverview({ passReel: PASS_REEL });
    render(
      <OverviewTab _error={scannerIssue({ workingTraceId: PASS_TRACE_ID })} />,
    );

    await user.click(screen.getByRole("tab", { name: /Agent Graph/ }));
    await user.click(splitButton());

    expect(
      screen.getByText("Could not load working trace spans. Please retry."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(passRefetch).toHaveBeenCalledTimes(1);
    expect(failRefetch).not.toHaveBeenCalled();
  });
});
