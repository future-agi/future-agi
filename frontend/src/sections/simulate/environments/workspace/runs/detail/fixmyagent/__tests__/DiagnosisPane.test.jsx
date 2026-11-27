import { describe, it, expect, vi } from "vitest";
import { render, screen, within, userEvent } from "src/utils/test-utils";

import DiagnosisPane from "../DiagnosisPane";
import { groupOneOffs } from "../oneOffs";

const way = (id, phrase, callIds, title = `${phrase} on ${callIds[0]}`) => ({
  id,
  phrase,
  title,
  callIds,
});

// Seven same-name one-offs in mixed case: one is clustered across 3 calls,
// two share a call, so 7 ways cover 8 unique calls.
const FARE = [
  way("f-1", "omits the fare", ["c1", "c2", "c3"]),
  way("f-2", "Omits the fare", ["c4"]),
  way("f-3", "omits the fare", ["c4"], "Second finding on c4"),
  way("f-4", "OMITS THE FARE", ["c5"]),
  way("f-5", "omits the fare", ["c6"]),
  way("f-6", "omits the fare", ["c7"]),
  way("f-7", "omits the fare", ["c8"]),
];
// Three findings, all on one call: more findings than calls.
const TONE = [
  way("t-1", "rude tone", ["c9"]),
  way("t-2", "rude tone", ["c9"], "Second finding on c9"),
  way("t-3", "rude tone", ["c9"], "Third finding on c9"),
];
// Two findings over two calls: fewer findings than TONE, more calls.
const SLOW = [
  way("s-1", "slow reply", ["c1"]),
  way("s-2", "slow reply", ["c2"]),
];

const analysis = (oneOffs) => ({
  status: "completed",
  isWorking: false,
  errorMessage: null,
  groupingPending: false,
  summary: {
    measuredCalls: 20,
    brokenGoals: 0,
    brokenCalls: 0,
  },
  goals: [],
  oneOffs,
});

const renderPane = (oneOffs, onViewCalls = vi.fn()) => {
  render(
    <DiagnosisPane
      analysis={analysis(oneOffs)}
      isLoading={false}
      isRequesting={false}
      onRetry={vi.fn()}
      onViewCalls={onViewCalls}
    />,
  );
  return onViewCalls;
};

const rowOf = (name) => screen.getByRole("button", { name }).parentElement;

describe("groupOneOffs", () => {
  it("merges names in any case and counts unique calls", () => {
    const groups = groupOneOffs([...TONE, ...FARE]);
    expect(groups.map((g) => [g.phrase, g.callIds.length])).toEqual([
      ["Omits the fare", 8],
      ["Rude tone", 1],
    ]);
  });

  it("sorts by unique calls, not by number of findings", () => {
    const groups = groupOneOffs([...TONE, ...SLOW]);
    expect(
      groups.map((g) => [g.phrase, g.ways.length, g.callIds.length]),
    ).toEqual([
      ["Slow reply", 2, 2],
      ["Rude tone", 3, 1],
    ]);
  });
});

describe("DiagnosisPane one-offs", () => {
  it("shows one row per name whose count matches its View link", async () => {
    const user = userEvent.setup();
    const onViewCalls = renderPane([...TONE, ...FARE]);

    expect(screen.getAllByText("Omits the fare")).toHaveLength(1);
    const fare = rowOf("Omits the fare");
    expect(within(fare).getByText("8")).toBeInTheDocument();
    await user.click(within(fare).getByRole("button", { name: "View" }));
    expect(onViewCalls).toHaveBeenLastCalledWith([
      "c1",
      "c2",
      "c3",
      "c4",
      "c5",
      "c6",
      "c7",
      "c8",
    ]);

    // The rude tone row counts its one call, not its three findings.
    expect(within(rowOf("Rude tone")).getByText("1")).toBeInTheDocument();
  });

  it("opens each call's explanation, 5 at a time, each with its own View", async () => {
    const user = userEvent.setup();
    const onViewCalls = renderPane(FARE);

    await user.click(screen.getByRole("button", { name: "Omits the fare" }));
    expect(screen.getByText("omits the fare on c1")).toBeInTheDocument();
    expect(screen.queryByText("omits the fare on c8")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getByText("omits the fare on c8")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();

    const detail = screen.getByText("Second finding on c4").parentElement;
    await user.click(within(detail).getByRole("button", { name: "View" }));
    expect(onViewCalls).toHaveBeenLastCalledWith(["c4"]);
  });

  it("counts the summary's one-offs by row, not by finding", () => {
    renderPane([...TONE, ...FARE]);
    expect(screen.getByText(/plus 2 one-off issues/)).toBeInTheDocument();
  });

  it("shows a name equal to its title in full, as plain text with nothing to expand", () => {
    renderPane([way("o-1", "omits the fare", ["c1", "c2"], "Omits The Fare")]);
    const name = screen.getByText("Omits the fare");
    expect(name).toBeInTheDocument();
    // Cut off with an ellipsis it would carry noWrap; plain text wraps.
    expect(name).not.toHaveClass("MuiTypography-noWrap");
    expect(screen.queryByRole("button", { name: "Omits the fare" })).toBeNull();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
    expect(screen.queryByText("Omits The Fare")).toBeNull();
  });

  it("does not expand when the title is the name with a subject and a full stop", () => {
    renderPane([
      way(
        "o-2",
        "echoes full card number",
        ["c1"],
        "Agent echoes full card number.",
      ),
    ]);
    expect(screen.getByText("Echoes full card number")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Echoes full card number" }),
    ).toBeNull();
  });

  it("lists every way of a mixed group, a plain one under the name, each with its View", async () => {
    const user = userEvent.setup();
    const onViewCalls = renderPane([
      way("m-1", "omits the fare", ["c1", "c2"], "Omits the fare"),
      way("m-2", "omits the fare", ["c3"], "Skips the fare on a transfer"),
    ]);

    await user.click(screen.getByRole("button", { name: "Omits the fare" }));
    const own = screen.getByText("Skips the fare on a transfer").parentElement;
    // The way without its own explanation reads as the name, below the row's.
    const plain = screen.getAllByText("Omits the fare")[1].parentElement;
    await user.click(within(own).getByRole("button", { name: "View" }));
    expect(onViewCalls).toHaveBeenLastCalledWith(["c3"]);
    await user.click(within(plain).getByRole("button", { name: "View" }));
    expect(onViewCalls).toHaveBeenLastCalledWith(["c1", "c2"]);
  });
});
