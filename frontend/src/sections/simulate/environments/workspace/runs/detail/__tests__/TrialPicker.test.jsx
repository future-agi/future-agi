import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import TrialPicker from "../TrialPicker";
import { trialsOf } from "../trialsOf";

const row = (id, trialIndex, status, sourceScenarioKey = "s-claim") => ({
  id,
  trialIndex,
  status,
  sourceScenarioKey,
  sourceScenario: sourceScenarioKey,
});

describe("trialsOf", () => {
  it("lists the loaded trials of the open call's scenario in trial order", () => {
    const rows = [
      row("c3", 3, "failed"),
      row("x1", 1, "passed", "s-other"),
      row("c1", 1, "passed"),
    ];
    expect(trialsOf(rows, row("c1", 1, "passed"))).toEqual([
      { id: "c1", trialIndex: 1, status: "passed" },
      { id: "c3", trialIndex: 3, status: "failed" },
    ]);
  });

  it("is empty with no open call", () => {
    expect(trialsOf([row("c1", 1, "passed")], null)).toEqual([]);
  });
});

describe("TrialPicker", () => {
  const trials = [
    { id: "c1", trialIndex: 1, status: "passed" },
    { id: "c2", trialIndex: 2, status: "failed" },
  ];

  it("names a trial's verdict as the rest of the run page does", () => {
    render(
      <TrialPicker
        trials={[
          { id: "c1", trialIndex: 1, status: "unmeasured" },
          { id: "c2", trialIndex: 2, status: "cancelled" },
        ]}
        currentId="c1"
        onPick={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Trial 1, Inconclusive" }),
    ).toBeInTheDocument();
  });

  it("shows each trial with its verdict and marks the open one", () => {
    render(<TrialPicker trials={trials} currentId="c2" onPick={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: "Trial 1, Passed" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("button", { name: "Trial 2, Failed" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("opens the trial picked", () => {
    const onPick = vi.fn();
    render(<TrialPicker trials={trials} currentId="c2" onPick={onPick} />);
    fireEvent.click(screen.getByRole("button", { name: "Trial 1, Passed" }));
    expect(onPick).toHaveBeenCalledWith("c1");
  });

  it("shows nothing for a scenario with one trial", () => {
    const { container } = render(
      <TrialPicker
        trials={trials.slice(0, 1)}
        currentId="c1"
        onPick={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
