import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { useErrorFeedOverview } from "src/api/errorFeed/error-feed";
import { useErrorFeedStore } from "../../store";
import { TRACE_STATUS } from "../../constants";
import OverviewTab from "../OverviewTab";

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedOverview: vi.fn(),
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentGraph", () => ({
  default: () => null,
}));
vi.mock("src/sections/projects/LLMTracing/GraphSection/AgentPath", () => ({
  default: () => null,
}));

// Synthetic display-only amounts, not billing records or captured API data.
// Keep the formatter and Overview binding real; expected text is not derived
// from formatExactMoney. The "Traces affected" row and the Trace Evidence
// header both show the recorded cost, never a token estimate.
/** @type {Array<{fixtureType: string, label: string, cost: string | number, expected: string}>} */
const SYNTHETIC_COST_CASES = [
  {
    fixtureType: "SYNTHETIC_DECIMAL_STRING_USD",
    label: "a synthetic high-precision decimal string",
    cost: "0.1234567890123456789",
    expected: "$0.1234567890123456789",
  },
  {
    fixtureType: "SYNTHETIC_NUMERIC_SUBCENT_USD",
    label: "a synthetic numeric sub-cent amount",
    cost: 0.000001,
    expected: "$0.000001",
  },
  {
    fixtureType: "SYNTHETIC_NUMERIC_ZERO_USD",
    label: "a genuine numeric zero",
    cost: 0,
    expected: "$0.00",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  useErrorFeedStore.setState({ selectedTraceIdByCluster: {} });
});

describe("Populated Overview trace-cost binding", () => {
  it.each(SYNTHETIC_COST_CASES)(
    "renders $label exactly",
    ({ cost, expected }) => {
      useErrorFeedOverview.mockReturnValue({
        isLoading: false,
        data: {
          events_over_time: [],
          pattern_summary: null,
          representative_total: 1,
          representative_traces: [
            {
              id: "synthetic-overview-money-trace",
              timestamp: "2000-01-01T00:00:00Z",
              status: TRACE_STATUS.FAIL,
              summary: {
                cost,
                input_tokens: 0,
                output_tokens: 0,
                latency_ms: 1,
              },
              evidence: { fail_reel: [], pass_reel: [] },
            },
          ],
        },
      });

      render(
        <OverviewTab
          _error={{ cluster_id: "synthetic-overview-money-cluster" }}
        />,
      );

      expect(useErrorFeedOverview).toHaveBeenCalledWith(
        "synthetic-overview-money-cluster",
      );
      expect(screen.getByText("synthetic-overview-money-trace")).toBeVisible();
      expect(screen.getByText("Trace Evidence", { exact: true })).toBeVisible();
      const shown = screen.getAllByText(expected, { exact: true });
      expect(shown).toHaveLength(2);
      shown.forEach((node) => expect(node).toBeVisible());
    },
  );
});
