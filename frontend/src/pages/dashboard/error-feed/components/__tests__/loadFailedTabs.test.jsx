import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import {
  useErrorFeedOverview,
  useErrorFeedTraces,
  useErrorFeedTrends,
} from "src/api/errorFeed/error-feed";
import { useErrorFeedStore } from "../../store";
import OverviewTab from "../OverviewTab";
import TracesTab from "../TracesTab";
import TrendsTab from "../TrendsTab";

// A failed tab request must say so and offer a retry, never draw a spinner
// that never ends or zeros that read as "this issue has no data".
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedOverview: vi.fn(),
  useErrorFeedTraces: vi.fn(),
  useErrorFeedTrends: vi.fn(),
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentGraph", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentPath", () => ({
  default: () => null,
}));
vi.mock("ag-grid-react", () => ({ AgGridReact: () => null }));
vi.mock("src/hooks/use-ag-theme", () => ({ useAgThemeWith: () => ({}) }));
vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "prototype" } }),
}));
vi.mock("src/sections/agents/helper", () => ({
  useVoiceCallDetail: () => ({ data: undefined, isFetching: false }),
}));
vi.mock("src/components/traceDetail/TraceDetailDrawerV2", () => ({
  default: () => null,
}));
vi.mock("src/components/VoiceDetailDrawerV2/VoiceDetailDrawerV2", () => ({
  default: () => null,
}));

const error = { cluster_id: "E-1", project_id: "project-1", trace_count: 42 };

const failed = (refetch) => ({
  data: undefined,
  isLoading: false,
  isFetching: false,
  isError: true,
  refetch,
});

const expectRetry = (refetch) => {
  const retry = screen.getByRole("button", { name: "Retry" });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  expect(refetch).toHaveBeenCalledTimes(1);
};

describe("Error Feed issue tabs when the request fails", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useErrorFeedStore.setState({ selectedTraceIdByCluster: {} });
  });

  it("Trends says it couldn't load and stops spinning", () => {
    const refetch = vi.fn();
    useErrorFeedTrends.mockReturnValue(failed(refetch));

    render(<TrendsTab error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load trends for this issue",
    );
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expectRetry(refetch);
  });

  it("Overview says it couldn't load, not that there is no evidence", () => {
    const refetch = vi.fn();
    useErrorFeedOverview.mockReturnValue(failed(refetch));

    render(<OverviewTab _error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load trace evidence for this issue",
    );
    expect(screen.queryByText("No trace evidence yet")).not.toBeInTheDocument();
    expect(screen.queryByText("Traces affected")).not.toBeInTheDocument();
    expectRetry(refetch);
  });

  it("Traces says it couldn't load instead of showing zero traces", () => {
    const refetch = vi.fn();
    useErrorFeedTraces.mockReturnValue(failed(refetch));

    render(<TracesTab error={error} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't load traces for this issue",
    );
    expect(screen.queryByText("Total traces")).not.toBeInTheDocument();
    expect(screen.queryByText(/Showing 0 of 0/)).not.toBeInTheDocument();
    expectRetry(refetch);
  });

  it("Trends keeps the data it already has when a refresh fails", () => {
    useErrorFeedTrends.mockReturnValue({
      ...failed(vi.fn()),
      data: {
        metrics: [],
        events_over_time: [],
        score_trends: [],
        activity_heatmap: [],
      },
    });

    render(<TrendsTab error={error} />);

    expect(screen.getByText("Last 14 days")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
