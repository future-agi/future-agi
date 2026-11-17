import { describe, expect, it } from "vitest";
import {
  STATUS_STYLES,
  etaText,
  getRunStatus,
  normalizeStatus,
  pollIntervalFor,
  runProgressOf,
} from "../runStatus";
import { runColorAt, runColorMap } from "../runColors";
import {
  fileSafeLabel,
  formatRunLabel,
  parseRunLabel,
  runLabel,
  runShortName,
  runTitle,
  variantLetter,
} from "../runLabel";
import { runMatches } from "../runMatch";

describe("runStatus", () => {
  it("relabels per C14 and §4", () => {
    expect(getRunStatus("Cancelled").label).toBe("Stopped");
    expect(getRunStatus("WaitingForResults").label).toBe("Waiting for results");
    expect(getRunStatus("ExperimentEvaluation").label).toBe("Scoring");
    // the capability contract: a partial run never reads the word Completed.
    expect(getRunStatus("PartialCompleted").label).toBe("Finished with errors");
    expect(getRunStatus("NotStarted").label).toBe("Not Started");
  });

  it("reads a canonical Run and normalises case and aliases", () => {
    expect(getRunStatus({ status: "Running" }).key).toBe("Running");
    expect(getRunStatus("completed").key).toBe("Completed");
    expect(normalizeStatus("stopped")).toBe("Cancelled");
    expect(normalizeStatus("nonsense")).toBeNull();
  });

  it("renders unknown statuses as raw text in neutral grey, never green", () => {
    const s = getRunStatus("Paused");
    expect(s.label).toBe("Paused");
    expect(s.known).toBe(false);
    expect(s.color).not.toBe(STATUS_STYLES.Completed.color);
    expect(s.bg).toBe(STATUS_STYLES.NotStarted.bg);
    expect(getRunStatus(null).label).toBe("Unknown");
  });

  it("marks active and terminal statuses", () => {
    expect(getRunStatus("Running").active).toBe(true);
    expect(getRunStatus("Running").spin).toBe(true);
    expect(getRunStatus("Completed").terminal).toBe(true);
    expect(getRunStatus("Cancelled").terminal).toBe(true);
    expect(getRunStatus("WaitingForResults").waiting).toBe(true);
    expect(getRunStatus("Editing").active).toBe(false);
  });

  it("polls 5 s while active, 30 s while waiting, stops when terminal", () => {
    expect(
      pollIntervalFor([{ status: "Completed" }, { status: "Running" }]),
    ).toBe(5000);
    expect(pollIntervalFor([{ status: "WaitingForResults" }])).toBe(30000);
    expect(
      pollIntervalFor([
        { status: "Completed" },
        { status: "Failed" },
        { status: "Cancelled" },
      ]),
    ).toBe(false);
    expect(pollIntervalFor([])).toBe(false);
    expect(pollIntervalFor(null)).toBe(false);
  });

  it("reads progress without NaN", () => {
    expect(
      runProgressOf({ progress: { done: 142, total: 248, errors: 2 } }),
    ).toMatchObject({
      done: 142,
      total: 248,
      errors: 2,
      left: 106,
      pct: 57,
    });
    expect(runProgressOf({ examples: 20 })).toMatchObject({
      done: 0,
      total: 20,
      pct: 0,
    });
    expect(runProgressOf(null)).toMatchObject({ done: 0, total: 0, pct: 0 });
  });

  it("words the ETA", () => {
    expect(etaText(null)).toBeNull();
    expect(etaText(30)).toBe("Less than a minute left");
    expect(etaText(130)).toBe("About 2 min left");
  });
});

describe("runColors", () => {
  it("gives the baseline index 0, then view order", () => {
    const m = runColorMap(["RUN_4A", "RUN_3A", "RUN_4B"], "RUN_3A");
    expect(m.RUN_3A.index).toBe(0);
    expect(m.RUN_4A.index).toBe(1);
    expect(m.RUN_4B.index).toBe(2);
    expect(m.RUN_3A.solid).toBe(runColorAt(0).solid);
    expect(m.RUN_4A.solid).not.toBe(m.RUN_3A.solid);
  });

  it("ignores a baseline outside the view and duplicate ids", () => {
    const m = runColorMap(["RUN_2A", "RUN_2B", "RUN_2A"], "RUN_1A");
    expect(Object.keys(m)).toEqual(["RUN_2A", "RUN_2B"]);
    expect(m.RUN_2A.index).toBe(0);
  });

  it("returns solid, tint, fg and light colours", () => {
    const c = runColorAt(1);
    expect(c.solid).toMatch(/^#|rgb/);
    expect(c.tint).toMatch(/rgba/);
    expect(c.fg).toBeTruthy();
    expect(c.light).toBeTruthy();
  });
});

describe("runLabel", () => {
  const run4b = {
    run_id: "RUN_4B",
    experiment_ordinal: 4,
    letter: "B",
    name: "Support Agent v3",
  };
  it("builds and parses #4B", () => {
    expect(runLabel(run4b)).toBe("#4B");
    expect(runLabel({ ...run4b, label: "#4B" })).toBe("#4B");
    expect(parseRunLabel("#12C")).toEqual({ ordinal: 12, letter: "C" });
    expect(parseRunLabel("4A")).toEqual({ ordinal: 4, letter: "A" });
    expect(parseRunLabel("x")).toBeNull();
    expect(runTitle(run4b)).toBe("#4B Support Agent v3");
  });

  it("W1-1: reads and writes the label of a later run of a config", () => {
    ["#2A·2", "#2A.2", "#2A-2", "#2A 2", "2A·2"].forEach((typed) =>
      expect(parseRunLabel(typed)).toEqual({
        ordinal: 2,
        letter: "A",
        attempt: 2,
      }),
    );
    expect(parseRunLabel("#9A·14")).toEqual({
      ordinal: 9,
      letter: "A",
      attempt: 14,
    });
    expect(formatRunLabel({ ordinal: 2, letter: "A", attempt: 2 })).toBe(
      "#2A·2",
    );
    expect(formatRunLabel({ ordinal: 2, letter: "A", attempt: 1 })).toBe("#2A");
    expect(fileSafeLabel("#2A·2")).toBe("2A-2");
    expect(fileSafeLabel("#4B")).toBe("4B");
    expect(runMatches({ label: "#2A·2" }, "#2A.2")).toBe(true);
    expect(runMatches({ label: "#2A·2" }, "2A 2")).toBe(true);
    expect(runMatches({ label: "#2A" }, "#2A.2")).toBe(false);
  });

  it("letters variants past Z", () => {
    expect(variantLetter(0)).toBe("A");
    expect(variantLetter(25)).toBe("Z");
    expect(variantLetter(26)).toBe("AA");
    expect(variantLetter(-1)).toBe("");
  });

  it("derives a short name from the target when the run has none", () => {
    expect(
      runShortName({
        target_type: "prompt",
        target: {
          prompt_name: "Support Reply",
          prompt_version_name: "V4",
          model: "gpt-4o-mini",
        },
      }),
    ).toBe("Support Reply V4 × gpt-4o-mini");
    expect(
      runShortName({
        target_type: "external",
        target: { branch: "main", commit: "a1f3c9" },
      }),
    ).toBe("main@a1f3c9");
    expect(
      runShortName({
        target_type: "http",
        target: { url: "api.acme.ai/v1/reply" },
      }),
    ).toBe("POST api.acme.ai/v1/reply");
    expect(runShortName(null)).toBe("");
  });
});

describe("W1-1 · download names", () => {
  it("a comparison file names each run file-safe", async () => {
    const { compareFileBase } = await import("../runLabel");
    expect(compareFileBase(["#2A·2", "#2B"])).toBe("compare-2A-2-2B");
    expect(compareFileBase(["#2A", "#2B"])).toBe("compare-2A-2B");
  });
});
