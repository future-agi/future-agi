import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import {
  CALLS,
  callRow,
  environmentResult,
  fetchCallsFrom,
  runsResult,
} from "./compareFixtures";
import { renderComparePage } from "./renderComparePage";
import { mapCallRow } from "src/api/simulate-environments/runCalls";
import { buildComparison } from "../useComparison";
import { pairedInterval } from "../pairedInterval";
import { pairsBetween, readComparison } from "../compareStats";
import CompareVerdict from "../CompareVerdict";

const useEnvironment = vi.fn();
vi.mock(
  "src/api/simulate-environments/environment",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEnvironment: (...args) => useEnvironment(...args),
  }),
);

const useEnvironmentRuns = vi.fn();
vi.mock("src/api/simulate-environments/runs", async (importOriginal) => ({
  ...(await importOriginal()),
  useEnvironmentRuns: (...args) => useEnvironmentRuns(...args),
}));

const fetchRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRunCalls: (...args) => fetchRunCalls(...args),
}));

// Each call's transcript, as the call drawer's detail hook maps it.
const useCallDetail = vi.fn();
vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallDetail: (...args) => useCallDetail(...args),
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));

// The distribution chart, reduced to the series it was given.
vi.mock("react-apexcharts", () => ({
  default: ({ series }) => (
    <div
      data-testid="distribution-chart"
      data-series={JSON.stringify(series)}
    />
  ),
}));

/*
  Vel's pairedInterval, verbatim from the prototype (feat/simulation-studio-v2
  @ f0b6d8d8c, frontend/src/sections/simulate-v2/_mock/pairs.js:286-310), kept
  here as the reference our port must match.
*/
const DEFAULT_BETWEEN = 0.035;
const Z95 = 1.96;
const smooth = (p, k) => (p * k + 0.5) / (k + 1);
const mean = (xs) =>
  xs.length ? xs.reduce((a, x) => a + x, 0) / xs.length : 0;
const velPairedInterval = (diffs) => {
  const n = diffs.length;
  if (!n) return { n: 0, delta: null, lo: null, hi: null, half: null };
  const d = diffs.map((x) => x.b - x.a);
  const m = mean(d);
  const s2 =
    n > 1 ? d.reduce((a, x) => a + (x - m) ** 2, 0) / (n - 1) : DEFAULT_BETWEEN;
  const trial = mean(
    diffs.map((x) => {
      const pa = smooth(x.a, x.ka);
      const pb = smooth(x.b, x.kb);
      return (pa * (1 - pa)) / x.ka + (pb * (1 - pb)) / x.kb;
    }),
  );
  const half = Z95 * Math.sqrt(Math.max(s2, trial) / n);
  const r = (v) => Math.round(v * 1000) / 10;
  return {
    n,
    delta: r(m),
    lo: r(m - half),
    hi: r(m + half),
    half: r(half),
    s2,
    trial,
  };
};

// Vel's worked example: 32 scenarios at three repeats a side.
const THIRDS = [0, 1 / 3, 2 / 3, 1];
const VEL_SAMPLE = Array.from({ length: 32 }, (_, i) => ({
  a: THIRDS[i % 4],
  b: THIRDS[(i * 3 + 1) % 4],
  ka: 3,
  kb: 3,
}));

// `n` scenarios, each run's calls passing or failing as told, `k` trials each.
const callsFor = (n, outcomeOf, k = 1) =>
  Array.from({ length: n }, (_, i) =>
    Array.from({ length: k }, (__, t) =>
      callRow(`c-${i}-${t}`, `s-${i}`, outcomeOf(i), { trial_index: t + 1 }),
    ),
  ).flat();

const comparisonOf = (base, target) =>
  buildComparison(["a", "b"], {
    a: { tasks: base, evalColumns: [] },
    b: { tasks: target, evalColumns: [] },
  });

// buildComparison takes mapped tasks; map the raw rows the way the API does.
const mapped = (rows) => rows.map((r) => mapCallRow(r, []));

beforeEach(() => {
  useEnvironment.mockReturnValue(environmentResult());
  useEnvironmentRuns.mockReturnValue(runsResult());
  fetchRunCalls.mockImplementation(fetchCallsFrom());
  useCallDetail.mockImplementation((id) => ({
    callDetail: id
      ? {
          id,
          turns: [
            { role: "customer", text: `Caller opens ${id}` },
            { role: "agent", text: `Agent reply in ${id}` },
          ],
        }
      : null,
    isLoading: false,
  }));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Compare verdict", () => {
  it("pairedInterval on shared scenarios matches Vel's function on its own sample", async () => {
    expect(pairedInterval(VEL_SAMPLE)).toEqual(velPairedInterval(VEL_SAMPLE));
    expect(pairedInterval([])).toEqual(velPairedInterval([]));
    const one = [{ a: 1, b: 0, ka: 1, kb: 3 }];
    expect(pairedInterval(one)).toEqual(velPairedInterval(one));

    // And on the pairs our own comparison builds from real calls.
    const comparison = buildComparison(["r1", "r3"], {
      r1: { tasks: mapped(CALLS.r1), evalColumns: [] },
      r3: { tasks: mapped(CALLS.r3), evalColumns: [] },
    });
    const pairs = pairsBetween(comparison.rows, "r1", "r3");
    // refund (0 → 2 of 2), address (1 → 0) and greet (1 → 1); cancel is new.
    expect(pairs).toEqual([
      { id: "refund", a: 0, b: 1, ka: 1, kb: 2 },
      { id: "address", a: 1, b: 0, ka: 1, kb: 1 },
      { id: "greet", a: 1, b: 1, ka: 1, kb: 1 },
    ]);
    expect(pairedInterval(pairs)).toEqual(velPairedInterval(pairs));
  });

  it("verdict reads Improved / Regressed / No meaningful change with the interval; noise band and held-fixed are soon chips", async () => {
    const pass = () => "passed";
    const fail = () => "failed";
    const cases = [
      [mapped(callsFor(12, fail)), mapped(callsFor(12, pass)), "Improved"],
      [mapped(callsFor(12, pass)), mapped(callsFor(12, fail)), "Regressed"],
      [
        mapped(callsFor(40, pass, 3)),
        mapped(callsFor(40, pass, 3)),
        "No meaningful change",
      ],
    ];
    for (const [base, target, label] of cases) {
      const read = readComparison(comparisonOf(base, target), ["a", "b"]);
      const { unmount } = render(<CompareVerdict read={read} />);
      expect(screen.getByText(label)).toBeInTheDocument();
      const delta = read.interval.delta;
      expect(
        screen.getByText(
          `${delta > 0 ? "+" : delta < 0 ? "−" : "±"}${Math.abs(delta).toFixed(1)} pts`,
        ),
      ).toBeInTheDocument();
      fireEvent.mouseOver(
        screen.getByText(/pass rate, on \d+ of \d+ scenarios/),
      );
      expect(
        await screen.findByText(/95% interval [+−±][\d.]+ to [+−±][\d.]+ pts/),
      ).toBeInTheDocument();
      expect(screen.getByText("Noise band soon")).toBeInTheDocument();
      expect(screen.getByText("Held fixed soon")).toBeInTheDocument();
      unmount();
    }

    // Below ten pairs there is no aggregate.
    const few = readComparison(
      comparisonOf(mapped(callsFor(3, fail)), mapped(callsFor(3, pass))),
      ["a", "b"],
    );
    render(<CompareVerdict read={few} />);
    expect(screen.getByText("Only 3 comparable pairs.")).toBeInTheDocument();
  });
});

describe("Compare views", () => {
  const chooseView = (name) => {
    fireEvent.click(screen.getByRole("button", { name: "Display" }));
    fireEvent.click(screen.getByRole("button", { name }));
    fireEvent.keyDown(screen.getByRole("presentation"), { key: "Escape" });
  };

  it("summary, grid and distribution use real numbers", async () => {
    renderComparePage("?runs=r1,r3");
    await screen.findAllByTestId("compare-row");

    // The distribution bins each run's real Task done scores.
    const series = JSON.parse(
      screen.getByTestId("distribution-chart").dataset.series,
    );
    expect(series.map((s) => [s.data[2], s.data[9]])).toEqual([
      [1, 2],
      [1, 4],
    ]);

    chooseView("Summary view");
    const summary = screen.getByTestId("compare-summary");
    const line = (label) =>
      within(summary).getByText(label).closest("[data-testid=summary-line]");
    expect(line("Pass rate · 3 shared scenarios")).toHaveTextContent("67%");
    expect(line("Avg duration")).toHaveTextContent("30.0s");
    expect(line("Avg duration")).toHaveTextContent("28.0s");
    expect(line("Tokens")).toHaveTextContent("3,000");
    expect(line("Tokens")).toHaveTextContent("5,000");
    expect(line("Task done")).toHaveTextContent("67%");
    expect(line("Task done")).toHaveTextContent("80%");
    expect(within(summary).getByText("Cost soon")).toBeInTheDocument();

    chooseView("Grid view");
    const grid = screen.getByTestId("compare-grid");
    const refund = within(grid)
      .getByText("refund")
      .closest("[data-testid=grid-row]");
    expect(refund).toHaveTextContent("Failed");
    expect(refund).toHaveTextContent("Passed 2/2");
    expect(refund).toHaveTextContent("20%");
    expect(refund).toHaveTextContent("90%");
  });

  it("call compare shows each run's transcript and checklist; graph is a soon chip", async () => {
    const subGoals = (met) => [
      { name: "identity_verified", passed: true },
      { name: "refund_created", passed: met },
    ];
    fetchRunCalls.mockImplementation(
      fetchCallsFrom({
        r1: [
          callRow("c1-refund", "refund", "failed", {
            sub_goal_results: subGoals(false),
          }),
        ],
        r3: [
          callRow("c3-refund-1", "refund", "passed", {
            sub_goal_results: subGoals(true),
          }),
        ],
      }),
    );
    renderComparePage("?runs=r1,r3");
    fireEvent.click(await screen.findByText("refund"));

    const drawer = await screen.findByTestId("call-compare");
    expect(within(drawer).getByText("Comparing 2 runs")).toBeInTheDocument();
    expect(
      within(drawer).getByText("Agent reply in c1-refund"),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByText("Agent reply in c3-refund-1"),
    ).toBeInTheDocument();
    expect(useCallDetail).toHaveBeenCalledWith("c1-refund");
    expect(useCallDetail).toHaveBeenCalledWith("c3-refund-1");
    expect(within(drawer).getByText("Graph soon")).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole("tab", { name: "Checklist" }));
    expect(within(drawer).getByText("1 of 2 met")).toBeInTheDocument();
    expect(within(drawer).getByText("2 of 2 met")).toBeInTheDocument();
    expect(within(drawer).getAllByText("refund_created")).toHaveLength(2);
  });
});
