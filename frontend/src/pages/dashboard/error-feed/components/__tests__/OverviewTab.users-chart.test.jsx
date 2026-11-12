import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { useErrorFeedOverview } from "src/api/errorFeed/error-feed";
import { useErrorFeedStore } from "../../store";
import OverviewTab from "../OverviewTab";

// OBS-SW-153: the Overview chart draws the API's distinct users per day. It
// never invents users from the event count, and never sums per-day distinct
// users into a total that counts a returning user once per day.

const charts = vi.hoisted(() => []);

vi.mock("apexcharts", async (importOriginal) => {
  function RecordingApexCharts(_element, options) {
    charts.push(options);
    this.render = () => Promise.resolve();
    this.destroy = () => {};
  }
  return { ...(await importOriginal()), default: RecordingApexCharts };
});
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

function renderChart(eventsOverTime) {
  useErrorFeedOverview.mockReturnValue({
    isLoading: false,
    data: {
      events_over_time: eventsOverTime,
      pattern_summary: null,
      representative_total: 0,
      representative_traces: [],
    },
  });
  render(<OverviewTab _error={{ cluster_id: "synthetic-users-cluster" }} />);
  const users = charts.at(-1).series.find((s) => s.name === "Users");
  return users.data.map((point) => point.y);
}

// Legend label -> its label stack -> the legend item holding label and total.
const legendItem = (label) =>
  screen.getByText(label, { exact: true }).parentElement.parentElement;

beforeEach(() => {
  vi.clearAllMocks();
  charts.length = 0;
  useErrorFeedStore.setState({ selectedTraceIdByCluster: {} });
});

describe("Overview events and users chart", () => {
  it("draws the distinct users the API reports for each day", () => {
    const users = renderChart([
      { date: "2026-03-01", errors: 10, passing: 0, users: 3 },
      { date: "2026-03-02", errors: 5, passing: 0, users: 3 },
    ]);

    expect(users).toEqual([3, 3]);
    expect(legendItem("Events")).toHaveTextContent("Events15");
    // 3 + 3 would count a user active on both days twice.
    expect(legendItem("Users")).toHaveTextContent(/^Users$/);
  });

  it("leaves a day without a user count empty instead of estimating it", () => {
    const users = renderChart([{ date: "2026-03-01", errors: 10, passing: 0 }]);

    expect(users).toEqual([null]);
  });
});
