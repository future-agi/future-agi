import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { useGetTraceDetail } from "src/api/project/trace-detail";
import { useSharedIssueTrace } from "src/api/shared-links";
import {
  useErrorFeedOverview,
  useErrorFeedTrends,
} from "src/api/errorFeed/error-feed";
import TrendsTab from "../TrendsTab";
import OverviewTab, { TraceGraphView } from "../OverviewTab";
import { SharedIssueContext } from "../../sharedIssueContext";

vi.mock("src/api/project/trace-detail", () => ({ useGetTraceDetail: vi.fn() }));
vi.mock("src/api/shared-links", () => ({ useSharedIssueTrace: vi.fn() }));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedOverview: vi.fn(() => ({ data: undefined, isLoading: false })),
  useErrorFeedTrends: vi.fn(() => ({ data: undefined, isLoading: false })),
}));

const shared = {
  token: "tok",
  issue: { row: { cluster_id: "E-1" } },
  overview: {
    events_over_time: [],
    pattern_summary: null,
    representative_traces: [],
    representative_total: 0,
  },
  trends: {
    metrics: [{ label: "Error rate", value: "12%", delta: 0 }],
    events_over_time: [],
    score_trends: [],
    activity_heatmap: [],
  },
};

const inShared = (ui) =>
  render(
    <SharedIssueContext.Provider value={shared}>
      {ui}
    </SharedIssueContext.Provider>,
  );

describe("Error Feed tabs on a shared issue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useGetTraceDetail.mockReturnValue({ data: undefined, isLoading: false });
    useSharedIssueTrace.mockReturnValue({
      data: { observation_spans: [] },
      isLoading: false,
      isError: false,
    });
  });

  it("renders Trends from the shared payload without fetching", () => {
    inShared(<TrendsTab error={{ cluster_id: "E-1" }} />);

    expect(screen.getByText("Error rate")).toBeInTheDocument();
    expect(useErrorFeedTrends).toHaveBeenCalled();
    for (const [clusterId] of useErrorFeedTrends.mock.calls) {
      expect(clusterId).toBeFalsy();
    }
  });

  it("shows an empty Trends tab, not a spinner, when the share has no trends", () => {
    render(
      <SharedIssueContext.Provider value={{ ...shared, trends: null }}>
        <TrendsTab error={{ cluster_id: "E-1" }} />
      </SharedIssueContext.Provider>,
    );

    expect(
      screen.getByText("No trend data for this issue."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("renders Overview from the shared payload without fetching", () => {
    inShared(<OverviewTab _error={{ cluster_id: "E-1" }} />);

    expect(
      screen.getByText("No trace evidence available for this cluster yet."),
    ).toBeInTheDocument();
    for (const [clusterId] of useErrorFeedOverview.mock.calls) {
      expect(clusterId).toBeFalsy();
    }
  });

  it("loads trace evidence through the share token", () => {
    inShared(<TraceGraphView traceId="trace-1" mode="graph" />);

    expect(useSharedIssueTrace).toHaveBeenCalledWith("tok", "trace-1");
    for (const [traceId] of useGetTraceDetail.mock.calls) {
      expect(traceId).toBeFalsy();
    }
    expect(
      screen.getByText("No span data available for this trace"),
    ).toBeInTheDocument();
  });
});
