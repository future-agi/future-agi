import { beforeEach, describe, expect, it, vi } from "vitest";
import { format } from "date-fns";
import { render, screen, within } from "src/utils/test-utils";
import { useErrorFeedOverview } from "src/api/errorFeed/error-feed";
import { useErrorFeedStore } from "../../store";
import { TRACE_STATUS } from "../../constants";
import OverviewTab from "../OverviewTab";

// OBS-SW-152 / OBS-SW-154: the "Traces affected" list shows what was recorded.
// A trace whose spans aged out has no time, latency, tokens or cost; those
// read as a dash, not the epoch, "ms", "0 tok" or a token-priced "$0.0000".

vi.mock("src/api/errorFeed/error-feed", async (importOriginal) => ({
  ...(await importOriginal()),
  useErrorFeedOverview: vi.fn(),
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentGraph", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentPath", () => ({
  default: () => null,
}));

const TRACE_ID = "synthetic-overview-list-trace";
const RECORDED_AT = "2026-03-04T09:12:00Z";

function renderTrace({ timestamp = RECORDED_AT, summary }) {
  useErrorFeedOverview.mockReturnValue({
    isLoading: false,
    data: {
      events_over_time: [],
      pattern_summary: null,
      representative_total: 1,
      representative_traces: [
        {
          id: TRACE_ID,
          timestamp,
          status: TRACE_STATUS.FAIL,
          summary,
          evidence: {
            input: "Synthetic input",
            fail_reel: [],
            pass_reel: [],
          },
        },
      ],
    },
  });
  render(<OverviewTab _error={{ cluster_id: "synthetic-list-cluster" }} />);
  // Trace ID sits in the row's top line; its parent's parent is the row.
  return screen.getByText(TRACE_ID).parentElement.parentElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  useErrorFeedStore.setState({ selectedTraceIdByCluster: {} });
});

describe("Overview traces affected list", () => {
  it("shows dashes for a trace whose spans are gone", () => {
    const row = renderTrace({
      timestamp: null,
      summary: {
        latency_ms: null,
        input_tokens: null,
        output_tokens: null,
        cost: null,
      },
    });

    // Time, latency, cost and tokens.
    expect(within(row).getAllByText("—")).toHaveLength(4);
    expect(within(row).queryByText(/ms$/)).not.toBeInTheDocument();
    expect(within(row).queryByText(/tok$/)).not.toBeInTheDocument();
    expect(within(row).queryByText(/^\$/)).not.toBeInTheDocument();
  });

  it("dates each trace time so traces from different days differ", () => {
    const row = renderTrace({
      summary: { latency_ms: 1200, input_tokens: 0, output_tokens: 0 },
    });

    expect(
      within(row).getByText(format(new Date(RECORDED_AT), "MMM d, HH:mm")),
    ).toBeVisible();
    expect(within(row).getByText(`${(1200).toLocaleString()}ms`)).toBeVisible();
  });

  it("shows the recorded cost, not a fixed per-token estimate", () => {
    const row = renderTrace({
      summary: {
        latency_ms: 1200,
        input_tokens: 1000,
        output_tokens: 500,
        cost: 0.12,
      },
    });

    expect(within(row).getByText("$0.12")).toBeVisible();
    expect(
      within(row).getByText(`${(1500).toLocaleString()} tok`),
    ).toBeVisible();
    // The Trace Evidence header shows the same recorded amount.
    expect(screen.getAllByText("$0.12")).toHaveLength(2);
    expect(screen.queryByText("$0.0105")).not.toBeInTheDocument();
  });

  it("leaves an unknown cost unknown instead of pricing the tokens", () => {
    const row = renderTrace({
      summary: {
        latency_ms: 1200,
        input_tokens: 1000,
        output_tokens: 500,
        cost: null,
      },
    });

    expect(within(row).getByText("—")).toBeVisible();
    expect(screen.queryByText(/^\$/)).not.toBeInTheDocument();
  });
});
