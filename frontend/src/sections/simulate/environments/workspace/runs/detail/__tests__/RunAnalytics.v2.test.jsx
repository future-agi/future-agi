import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneElement } from "react";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import {
  AGENT_TYPES,
  chatAnalytics,
  voiceAnalytics,
} from "../analytics/__tests__/analyticsFixture";

const useRunAnalytics = vi.fn();
const useRunDetail = vi.fn();
vi.mock("recharts", async (importOriginal) => ({
  ...(await importOriginal()),
  ResponsiveContainer: ({ children }) =>
    cloneElement(children, { width: 600, height: 250 }),
}));
vi.mock("src/api/simulate-environments/runAnalytics", () => ({
  useRunAnalytics: (...args) => useRunAnalytics(...args),
}));
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunDetail: (...args) => useRunDetail(...args),
}));

const { default: RunAnalytics } = await import("../RunAnalytics");

const show = (data, props = {}) => {
  useRunAnalytics.mockReturnValue({ data, isPending: false, isError: false });
  return render(
    <RunAnalytics executionId="execution-1" envId="env-1" {...props} />,
  );
};
const tile = (label) => screen.getByText(label).parentElement;
const region = (name) => screen.getByRole("region", { name });

describe("RunAnalytics v2", () => {
  beforeEach(() => {
    const storage = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    });
    useRunAnalytics.mockReset();
    useRunDetail.mockReset();
    useRunDetail.mockImplementation((_testId, executionId) => ({
      identity: executionId === "execution-0" ? { ordinal: 4 } : null,
      isLoading: false,
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    ["voice", voiceAnalytics, "Call"],
    ["chat", chatAnalytics, "Chat"],
  ])(
    "call duration trend and percentiles read dashboard.series duration_ms and latency_percentiles (%s)",
    (_type, fixture, Noun) => {
      show(fixture());

      const trend = region(`${Noun} duration`);
      // Two of the three calls have a duration; the unmeasured one is a gap.
      expect(trend.querySelectorAll(".recharts-line-dot")).toHaveLength(2);
      const ticks = [...trend.querySelectorAll(".recharts-yAxis text")].map(
        (node) => node.textContent,
      );
      expect(ticks.length).toBeGreaterThan(0);
      expect(ticks.every((text) => text.endsWith("s"))).toBe(true);
      const top = Math.max(
        ...ticks.map((text) =>
          Number(text.replace(/s$/, "").replace(/,/g, "")),
        ),
      );
      expect(top).toBeGreaterThanOrEqual(308.1);

      const percentiles = region(`${Noun} duration percentiles`);
      expect(
        within(percentiles).getByText(
          "p50 189s · p90 308.1s · p99 338.5s of measured durations",
        ),
      ).toBeInTheDocument();
      expect(
        within(percentiles).queryByText("No measurements recorded"),
      ).not.toBeInTheDocument();

      // Agent latency keeps its own widgets (R22).
      expect(region("Agent latency")).toBeInTheDocument();
      expect(region("Agent response time percentiles")).toBeInTheDocument();
    },
  );

  it.each(AGENT_TYPES)(
    "tiles include Avg CSAT, Duration p90 and Total cost (%s)",
    async (_type, fixture) => {
      show(fixture());

      expect(within(tile("Avg CSAT Score")).getByText("7.4")).toBeVisible();
      expect(within(tile("Duration p90")).getByText("308.1s")).toBeVisible();
      expect(within(tile("Total cost")).getByText("$2.5")).toBeVisible();
      expect(screen.queryByText("CSAT satisfied")).not.toBeInTheDocument();

      // The satisfied share moved into the Avg CSAT hover (R22).
      fireEvent.mouseOver(within(tile("Avg CSAT Score")).getByText("7.4"));
      expect(
        await screen.findByText("1 of 2 scored at 8 or above (50%)"),
      ).toBeInTheDocument();
    },
  );

  it("chat runs read Total Chats, Completed, Completion (%) from the backend labels", () => {
    const chat = show(chatAnalytics());
    expect(within(tile("Total Chats")).getByText("6")).toBeVisible();
    expect(within(tile("Completed")).getByText("5")).toBeVisible();
    expect(within(tile("Completed")).getByText("of 6")).toBeVisible();
    expect(within(tile("Completion (%)")).getByText("83.3%")).toBeVisible();
    expect(screen.getByText("Chats passed")).toBeInTheDocument();
    for (const label of ["Total Calls", "Calls Connected (%)", "Agent WPM"]) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    chat.unmount();

    show(voiceAnalytics());
    expect(within(tile("Total Calls")).getByText("6")).toBeVisible();
    expect(within(tile("Connected")).getByText("5")).toBeVisible();
    expect(
      within(tile("Calls Connected (%)")).getByText("83.3%"),
    ).toBeVisible();
    expect(screen.getByText("Calls passed")).toBeInTheDocument();
    expect(screen.queryByText("Total Chats")).not.toBeInTheDocument();
  });

  it("voice sections are hidden on chat runs", () => {
    const chat = show(chatAnalytics());
    expect(
      screen.queryByRole("heading", { name: "Voice latency SLOs" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Voice pipeline latency" }),
    ).not.toBeInTheDocument();
    // The LLM cost a chat run records stays on the page.
    expect(region("Cost breakdown by pipeline stage")).toBeInTheDocument();
    chat.unmount();

    show(voiceAnalytics());
    expect(
      screen.getByRole("heading", { name: "Voice latency SLOs" }),
    ).toBeInTheDocument();
    expect(region("Voice pipeline latency")).toBeInTheDocument();
    expect(region("Cost breakdown by pipeline stage")).toBeInTheDocument();
  });

  it.each(AGENT_TYPES)(
    "regression banner shows newly passing and failing vs Run N; the pass-rate and cost deltas are soon chips (%s)",
    (_type, fixture) => {
      const view = show(fixture());
      const banner = screen.getByRole("alert");
      expect(banner).toHaveTextContent("vs Run 4");
      expect(banner).toHaveTextContent("1 scenario newly passing");
      expect(banner).toHaveTextContent("1 regressed");
      expect(banner).toHaveTextContent("compared on 2 scenarios");
      expect(within(banner).getByText("Pass rate delta soon")).toBeVisible();
      expect(within(banner).getByText("Cost delta soon")).toBeVisible();
      expect(useRunDetail).toHaveBeenCalledWith(undefined, "execution-0");
      view.unmount();

      // The previous run is not resolved yet: no invented number.
      useRunDetail.mockReturnValue({ identity: null, isLoading: true });
      const pending = show(fixture());
      expect(screen.getByRole("alert")).toHaveTextContent(
        "vs the previous run",
      );
      pending.unmount();

      const disjoint = fixture();
      disjoint.dashboard.comparison = {
        ...disjoint.dashboard.comparison,
        shared_scenarios: 0,
        newly_passing: [],
        newly_failing: [],
      };
      useRunDetail.mockReturnValue({ identity: { ordinal: 4 } });
      const unshared = show(disjoint);
      expect(screen.getByRole("alert")).toHaveTextContent(
        "No comparable scenarios vs Run 4",
      );
      unshared.unmount();

      const none = fixture();
      none.dashboard.comparison = {
        available: false,
        previous_execution_id: null,
        shared_scenarios: 0,
        newly_passing: [],
        newly_failing: [],
      };
      show(none);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it.each(AGENT_TYPES)(
    "failure attribution shows its empty state (%s)",
    (_type, fixture) => {
      show(fixture());
      const panel = region("Failure attribution");
      expect(
        within(panel).getByText(
          "Error Feed failure domains and retry policies are not recorded on simulation calls.",
        ),
      ).toBeVisible();
      expect(within(panel).getByText("Attribution soon")).toBeVisible();
    },
  );

  it.each([
    ["voice", voiceAnalytics, "How calls ended"],
    ["chat", chatAnalytics, "How chats ended"],
  ])(
    "only the goal donut drills into calls; other donuts say drill-down isn't in the API yet (%s)",
    async (_type, fixture, ended) => {
      const open = vi.fn();
      show(fixture(), { onOpenCalls: open });

      const goal = region("Goal outcome breakdown");
      fireEvent.click(
        within(goal).getByRole("button", { name: "Show Failed calls" }),
      );
      expect(open).toHaveBeenCalledWith({ goal_outcome: ["failed"] });
      expect(within(goal).queryByText("Drill-down soon")).toBeNull();

      for (const name of [ended, "Provider sentiment"]) {
        const donut = region(name);
        expect(
          within(donut).queryByRole("button", { name: /^Show / }),
        ).toBeNull();
        expect(within(donut).getByText("Drill-down soon")).toBeVisible();
      }
      fireEvent.mouseOver(within(region(ended)).getByText("Drill-down soon"));
      expect(
        await screen.findByText("Drill-down isn't in the API yet"),
      ).toBeInTheDocument();
    },
  );

  it.each(AGENT_TYPES)(
    "tool failure split and transport cost are soon chips (%s)",
    (_type, fixture) => {
      show(fixture());
      expect(
        within(region("Tool failure rate")).getByText("Failure split soon"),
      ).toBeVisible();
      const cost = region("Cost breakdown by pipeline stage");
      expect(within(cost).getByText("Transport cost soon")).toBeVisible();
      expect(
        within(cost).queryByText(/Transport cost is not recorded/),
      ).toBeNull();
      // The recorded components are still listed.
      expect(within(cost).getByText(/LLM \$2\.5/)).toBeInTheDocument();
    },
  );
});
