import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

const useCompareRuns = vi.fn();
vi.mock("src/api/simulate-environments/compareRuns", () => ({
  useCompareRuns: (...args) => useCompareRuns(...args),
}));

const { default: RunCompare } = await import("../RunCompare");

describe("RunCompare", () => {
  const env = { id: "env-1", versioning: { enabled: true } };
  const current = { executionId: "execution-1", label: "Run 2" };
  const against = { executionId: "execution-0", label: "Run 1" };
  const side = (passed, trials, evalRate = 100) => ({
    passed,
    trials,
    pass_rate: trials ? (passed / trials) * 100 : null,
    eval_pass_rate: evalRate,
  });
  const pair = (fields) => ({
    band: "paired",
    verdict: null,
    difference: null,
    interval: null,
    current: null,
    against: null,
    changed_sub_goals: [],
    reason: null,
    removed_in: null,
    ...fields,
  });
  const comparison = {
    execution_id: "execution-1",
    against_execution_id: "execution-0",
    pairs: [
      pair({
        scenario_key: "steady",
        scenario: "Steady refund",
        verdict: "no_meaningful_change",
        difference: 0,
        interval: { low: -56.14, high: 56.14 },
        current: side(3, 3),
        against: side(3, 3),
      }),
      pair({
        scenario_key: "worse",
        scenario: "Card dispute",
        verdict: "regressed",
        difference: -100,
        interval: { low: -100, high: -60.75 },
        current: side(0, 10, 0),
        against: side(10, 10),
      }),
      pair({
        scenario_key: "unsure",
        scenario: "Address change",
        verdict: "inconclusive",
        difference: 20,
        interval: { low: -26.43, high: 62.45 },
        current: side(5, 5),
        against: side(4, 5),
      }),
      pair({
        scenario_key: "fresh",
        scenario: "New greeting",
        band: "new",
        current: side(1, 1),
      }),
      pair({
        scenario_key: "gone",
        scenario: "Old upsell",
        band: "not_applicable",
        against: side(1, 1),
        reason: "removed in chat",
        removed_in: { id: "s3", label: "v3" },
      }),
      pair({
        scenario_key: "moved",
        scenario: "Refund window",
        band: "criteria_changed",
        verdict: "improved",
        difference: 100,
        interval: { low: 60.75, high: 100 },
        current: side(10, 10),
        against: side(0, 10),
        changed_sub_goals: ["refunded"],
      }),
    ],
    summary: {
      paired: 3,
      improved: 0,
      regressed: 1,
      no_meaningful_change: 1,
      inconclusive: 1,
      criteria_changed: 1,
      new: 1,
      not_applicable: 1,
      current_pass_rate: 65,
      against_pass_rate: 70,
      margin: 10,
    },
    versions: {
      current: {
        agent: { id: "a2", label: "v2" },
        environment: { id: "e2", label: "v2" },
        suite: { id: "s3", label: "v3" },
      },
      against: { agent: null, environment: null, suite: null },
    },
  };

  beforeEach(() => {
    useCompareRuns.mockReset();
    useCompareRuns.mockReturnValue({
      data: comparison,
      isPending: false,
      isError: false,
    });
  });

  const renderCompare = (props = {}) =>
    render(
      <RunCompare
        open
        onClose={vi.fn()}
        env={env}
        current={current}
        against={against}
        {...props}
      />,
    );
  const section = () => screen.getByRole("region", { name: "Compare runs" });
  const row = (name) => within(section()).getByText(name).closest("tr");

  it("compares the two runs it is given, naming both", () => {
    renderCompare();

    expect(useCompareRuns).toHaveBeenCalledWith(
      "env-1",
      "execution-1",
      "execution-0",
      { enabled: true },
    );
    expect(
      within(section()).getByText(
        "Run 2: Agent v2 · Environment v2 · Scenarios v3 · Run 1: before versioning",
      ),
    ).toBeInTheDocument();
    expect(
      within(section()).getByText(
        "1 regressed · 1 no meaningful change · 1 inconclusive · 1 new · 1 n/a · 1 criteria changed",
      ),
    ).toBeInTheDocument();
    expect(within(section()).queryByRole("combobox")).not.toBeInTheDocument();
    for (const name of ["Run 2", "Run 1"])
      expect(
        within(section()).getByRole("columnheader", { name }),
      ).toBeInTheDocument();
  });

  it("asks nothing while closed", () => {
    renderCompare({ open: false });

    expect(useCompareRuns).toHaveBeenLastCalledWith(
      "env-1",
      "execution-1",
      "execution-0",
      { enabled: false },
    );
    expect(
      screen.queryByRole("region", { name: "Compare runs" }),
    ).not.toBeInTheDocument();
  });

  it("shows each scenario's verdict and band", () => {
    renderCompare();

    expect(
      within(row("Steady refund")).getByText("No meaningful change"),
    ).toBeInTheDocument();
    expect(
      within(row("Card dispute")).getByText("Regressed"),
    ).toBeInTheDocument();
    // The verdict reads in body text; its series color is only on a dot.
    expect(within(row("Card dispute")).getByText("Regressed")).not.toHaveStyle({
      color: "#f56b74",
    });
    // Every verdict carries its interval, not only an inconclusive one.
    expect(
      within(row("Steady refund")).getByText("95% range -56.1 to +56.1 pts"),
    ).toBeInTheDocument();
    expect(
      within(row("Card dispute")).getByText("-100 pts"),
    ).toBeInTheDocument();
    const unsure = row("Address change");
    expect(within(unsure).getByText("Inconclusive")).toBeInTheDocument();
    expect(
      within(unsure).getByText("95% range -26.4 to +62.5 pts"),
    ).toBeInTheDocument();
    expect(within(row("New greeting")).getByText("New")).toBeInTheDocument();
    const gone = row("Old upsell");
    expect(within(gone).getByText("N/A")).toBeInTheDocument();
    expect(
      within(gone).getByText("Removed in Scenarios v3 (in chat)"),
    ).toBeInTheDocument();
    const moved = row("Refund window");
    expect(within(moved).getByText("Criteria changed")).toBeInTheDocument();
    expect(
      within(moved).getByText("Sub-goal changed: refunded"),
    ).toBeInTheDocument();
    // The worst change comes first.
    const rows = within(section()).getAllByRole("row");
    expect(within(rows[1]).getByText("Card dispute")).toBeInTheDocument();
  });

  it("shows the comparison loading", () => {
    useCompareRuns.mockReturnValue({
      data: undefined,
      isPending: true,
      isError: false,
    });
    renderCompare();

    expect(within(section()).getByText("Comparing runs…")).toBeInTheDocument();
  });

  it("says when the runs can't be compared", () => {
    useCompareRuns.mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
    });
    renderCompare();

    expect(
      within(section()).getByText(
        "Couldn't compare these runs. Close this and select two other runs.",
      ),
    ).toBeInTheDocument();
  });

  it("counts the trials nothing decided", () => {
    useCompareRuns.mockReturnValue({
      data: {
        ...comparison,
        pairs: [
          pair({
            scenario_key: "open",
            scenario: "Judge outage",
            verdict: "inconclusive",
            current: { ...side(1, 3), undecided: 2 },
            against: { ...side(3, 3), undecided: 0 },
          }),
        ],
      },
      isPending: false,
      isError: false,
    });
    renderCompare();

    expect(
      within(row("Judge outage")).getByText("1 / 3 trials · 2 undecided"),
    ).toBeInTheDocument();
    expect(
      within(row("Judge outage")).getByText("3 / 3 trials"),
    ).toBeInTheDocument();
  });

  it("names which run a one-sided scenario is missing from", () => {
    useCompareRuns.mockReturnValue({
      data: {
        ...comparison,
        pairs: [
          pair({
            scenario_key: "later",
            scenario: "Added later",
            band: "new",
            against: side(1, 1),
          }),
          pair({
            scenario_key: "skipped",
            scenario: "Left out there",
            band: "not_applicable",
            current: side(1, 1),
          }),
        ],
      },
      isPending: false,
      isError: false,
    });
    renderCompare();

    expect(
      within(row("Added later")).getByText("Only in the compared run"),
    ).toBeInTheDocument();
    expect(
      within(row("Left out there")).getByText("Not in the compared run"),
    ).toBeInTheDocument();
  });
});
