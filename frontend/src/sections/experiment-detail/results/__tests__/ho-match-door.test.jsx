// HO results R9 (W5 B-H5, door half): the results page's Candidate menu and
// the leaderboard's `Compare with…` menu open W5 B7's one match-by-key
// dialog with their run as run A; with no match endpoint the item is absent,
// and the leaderboard's item shows only while the tab's host is mounted.
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CandidateSelect from "../CandidateSelect";
import CompareWithPicker from "src/sections/develop-detail/ExperimentTab/CompareWithPicker";
import MatchDoorHost from "src/sections/common/Leaderboards/MatchDoorHost";
import {
  openMatchDoor,
  registerMatchHost,
} from "src/sections/common/Leaderboards/matchDoor";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));
let hasMatch = true;
vi.mock("src/sections/common/Analysis/workbench/w5Api", () => ({
  hasW5: (name) => name === "resultsMatch" && hasMatch,
}));
vi.mock("src/sections/common/CompareRuns/MatchByKeyDialog", () => ({
  default: ({ runs, defaultRunId }) => (
    <div data-testid="match-stub">
      {`${defaultRunId}|${runs.map((r) => r.run_id).join(",")}`}
    </div>
  ),
}));

const runs = [
  { run_id: "r1", label: "#1A", name: "Reply v1", experiment_id: "e1" },
  { run_id: "r2", label: "#2A", name: "Reply v2", experiment_id: "e2" },
  {
    run_id: "r3",
    label: "#2B",
    name: "Reply v2",
    experiment_id: "e2",
    listed: false,
  },
];

describe("Candidate menu · Compare with a run on another dataset…", () => {
  beforeEach(() => {
    hasMatch = true;
  });

  it("opens the match dialog with the candidate as run A", () => {
    render(
      <CandidateSelect
        runs={runs.slice(0, 2)}
        value="r2"
        baselineId="r1"
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("candidate-select"));
    const item = screen.getByTestId("candidate-match-other-dataset");
    expect(item.textContent).toBe("Compare #2A with a run on another dataset…");
    fireEvent.click(item);
    expect(screen.getByTestId("match-stub").textContent).toBe("r2|r1,r2");
  });

  it("is absent when the match endpoint is not there", () => {
    hasMatch = false;
    render(
      <CandidateSelect
        runs={runs.slice(0, 2)}
        value="r2"
        baselineId="r1"
        onChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("candidate-select"));
    expect(screen.queryByTestId("candidate-match-other-dataset")).toBeNull();
  });
});

describe("Leaderboard Compare with… · A run on another dataset…", () => {
  beforeEach(() => {
    hasMatch = true;
  });

  const openMenu = () =>
    fireEvent.click(
      screen.getByRole("button", { name: "Compare #1A with another run" }),
    );

  it("opens the tab's match dialog with the row's run and the listed runs", () => {
    render(
      <>
        <CompareWithPicker run={runs[0]} runs={runs} onPick={() => {}} />
        <MatchDoorHost />
      </>,
    );
    openMenu();
    fireEvent.click(screen.getByTestId("compare-with-other-dataset"));
    expect(screen.getByTestId("match-stub").textContent).toBe("r1|r1,r2");
  });

  it("keeps the dialog when the row's cell is re-created", () => {
    const { rerender } = render(
      <>
        <CompareWithPicker
          key="a"
          run={runs[0]}
          runs={runs}
          onPick={() => {}}
        />
        <MatchDoorHost />
      </>,
    );
    openMenu();
    fireEvent.click(screen.getByTestId("compare-with-other-dataset"));
    rerender(
      <>
        <CompareWithPicker
          key="b"
          run={runs[0]}
          runs={runs}
          onPick={() => {}}
        />
        <MatchDoorHost />
      </>,
    );
    expect(screen.getByTestId("match-stub").textContent).toBe("r1|r1,r2");
  });

  it("is absent without the tab's host", () => {
    render(<CompareWithPicker run={runs[0]} runs={runs} onPick={() => {}} />);
    openMenu();
    expect(screen.queryByTestId("compare-with-other-dataset")).toBeNull();
  });

  it("is absent when the match endpoint is not there", () => {
    hasMatch = false;
    render(
      <>
        <CompareWithPicker run={runs[0]} runs={runs} onPick={() => {}} />
        <MatchDoorHost />
      </>,
    );
    openMenu();
    expect(screen.queryByTestId("compare-with-other-dataset")).toBeNull();
  });
});

describe("matchDoor", () => {
  it("closes when the last host unmounts", () => {
    const off = registerMatchHost();
    openMatchDoor({ runs: runs.slice(0, 1), defaultRunId: "r1" });
    off();
    render(<MatchDoorHost />);
    expect(screen.queryByTestId("match-stub")).toBeNull();
  });
});
