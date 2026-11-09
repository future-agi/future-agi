// W1-1 kept runs: the rerun dialogs and toasts say a rerun starts a new run
// and the old one keeps its results (notify DESIGN §1.2 "Copy", word for
// word), and never use the internal word for a numbered run.
import { describe, expect, it } from "vitest";
import {
  RERUN_TOOLTIP,
  leaderboardRerunBody,
  leaderboardRerunFailedBody,
  nextRunLabel,
  rerunAllBody,
  rerunChangedBody,
  rerunExperimentBody,
  rerunFailedBody,
  rerunRunBody,
  rerunSomeBody,
  startedRunsOf,
  startedToast,
} from "../rerunCopyModel";

const a = { label: "#1A", attempts_count: 1 };
const b = { label: "#1B", attempts_count: 1 };

describe("W1-1 rerun copy", () => {
  it("names the next run of each config", () => {
    expect(nextRunLabel(a)).toBe("#1A·2");
    expect(
      nextRunLabel({ label: "#9A·13", attempt: 13, attempts_count: 13 }),
    ).toBe("#9A·14");
    expect(nextRunLabel({ label: "#4B", attempts_count: 3 })).toBe("#4B·4");
  });

  it("the eight bodies, word for word", () => {
    expect(
      rerunAllBody({ rowNoun: "row", allLine: "248 rows", runs: [a, b] }),
    ).toBe(
      "Every run runs again on every row (248 rows) as a new run: #1A·2 and #1B·2. #1A and #1B keep their results.",
    );
    expect(rerunFailedBody({ rowNoun: "row", runs: [a] })).toBe(
      "Only the rows that failed a check or errored run again, in a new run #1A·2. The rows that passed are carried over from #1A, so #1A·2 is complete and #1A stays as it was.",
    );
    expect(
      rerunChangedBody({
        secondary: "#2A's prompt changed",
        rowNoun: "row",
        runs: [{ label: "#2A" }],
      }),
    ).toBe(
      "#2A's prompt changed. Each runs again on every row as a new run (#2A·2); the other runs keep their results.",
    );
    expect(rerunExperimentBody()).toBe(
      "Every run of this experiment runs again on every example as a new run. The current runs keep their results.",
    );
    expect(rerunRunBody({ label: "#4B", attempts_count: 1 })).toBe(
      "Every example runs again in a new run, #4B·2. #4B keeps its results.",
    );
    expect(leaderboardRerunBody([a])).toBe(
      "Every row runs again in a new run and is scored again. This run keeps its results.",
    );
    expect(leaderboardRerunFailedBody()).toBe(
      "Only rows that errored or were stopped run again, in a new run. Completed rows are carried over.",
    );
    expect(
      rerunSomeBody({ rowNoun: "row", allLine: "20 rows", runs: [a] }),
    ).toBe(
      "#1A run again on every row (20 rows) as new runs: #1A·2. #1A keeps its results. The other runs keep theirs: Future AGI cannot start them again.",
    );
  });

  it("the toasts", () => {
    expect(startedToast(["#2A·2", "#2B·2"], ["#2A", "#2B"])).toBe(
      "Started #2A·2 and #2B·2. #2A and #2B keep their results.",
    );
    expect(startedToast(["#4B·2"], ["#4B"])).toBe(
      "Started #4B·2. #4B keeps its results.",
    );
    expect(startedToast([], ["#4B"])).toBe("");
  });

  it("reads the new runs from the response, and accepts the backend's text", () => {
    expect(
      startedRunsOf({
        data: { result: { run_ids: ["c1"], labels: ["#2A·2"], message: "x" } },
      }),
    ).toEqual({ runIds: ["c1"], labels: ["#2A·2"] });
    expect(
      startedRunsOf({
        data: { result: "Re-Run has started for the experiments." },
      }),
    ).toEqual({ runIds: [], labels: [] });
  });

  it("never says the internal word on screen", () => {
    const all = [
      rerunAllBody({ runs: [a] }),
      rerunFailedBody({ runs: [a, b] }),
      rerunChangedBody({ runs: [a] }),
      rerunExperimentBody(),
      rerunRunBody(a),
      leaderboardRerunBody([a, b]),
      leaderboardRerunFailedBody(),
      startedToast(["#1A·2"], ["#1A"]),
    ].join(" ");
    expect(all).not.toMatch(/attempt/i);
  });

  it("the Rerun tooltip says a rerun is a new run, never in place (review M5)", () => {
    expect(RERUN_TOOLTIP).toMatch(/new run/);
    expect(RERUN_TOOLTIP).toMatch(/keep their results/);
    expect(RERUN_TOOLTIP).not.toMatch(/in place|attempt/i);
  });

  it("the server's Started line joins like the toast", () => {
    expect(startedToast(["#2A·2", "#2B·2", "#2F·2", "#2G·2"])).toBe(
      "Started #2A·2, #2B·2, #2F·2 and #2G·2.",
    );
  });
});

describe("W1-1 · the Edit confirm never says re-run in place", () => {
  it("names the new runs and who keeps results", async () => {
    const { editKeptLine } = await import("../rerunCopyModel");
    expect(editKeptLine(["#4A"], [{ label: "#4A", attempts_count: 1 }])).toBe(
      "A changed run starts a new run: #4A·2. #4A keeps its results.",
    );
    expect(
      editKeptLine(
        ["#4A·2", "#4B"],
        [
          { label: "#4A·2", attempt: 2, attempts_count: 2 },
          { label: "#4B", attempts_count: 1 },
        ],
      ),
    ).toBe(
      "Changed runs start new runs: #4A·3 and #4B·2. #4A·2 and #4B keep their results.",
    );
    expect(editKeptLine([], [])).toBe("Results of kept runs stay as they are.");
  });
});
