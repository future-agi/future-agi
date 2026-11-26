import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

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
// Two ways on one call: more ways than calls, so it sorts below the fare.
const TONE = [
  way("t-1", "rude tone", ["c9"]),
  way("t-2", "rude tone", ["c9"], "Second finding on c9"),
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
    oneOffs: oneOffs.length,
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

    // The rude tone row counts its one call, not its two findings.
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
});
