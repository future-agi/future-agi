// The Edit confirm's second line and the footer total's text, per kind of
// save: an evaluation edited (alone, or with re-run prompts and a kept agent,
// which starts a new run of each changed prompt, says so first, then the
// same sentence, and shows the re-run estimate), an evaluation added,
// an evaluation edited and another added, and every other save, which keeps
// today's words. A save that starts runs and also scores kept runs says so
// after its amount, and its confirm line ends with the sentence for what
// changed.
import { describe, expect, it } from "vitest";
import {
  editChipText,
  editKeptLine,
  editKeptScoring,
  editSaveKind,
  editScoresKeptRuns,
} from "../rerunCopyModel";

const runs = [
  { label: "#4A", attempts_count: 1 },
  { label: "#4B", attempts_count: 1 },
];
const EDITED = "The edited evaluation is scored again on every run.";
const ADDED = "The added evaluation is scored on every run.";
const EDITED_AND_ADDED =
  "The edited and added evaluations are scored on every run.";
const NOT_ESTIMATED = "Scoring on kept runs is not included in this estimate.";
// Words no save shows any more: that some kept runs' scores are cleared and
// how to score them.
const RETIRED = /runs that re-run|cleared|run the evaluation again/;
// A lower-case word right after a full stop.
const FRAGMENT = /\. [a-z]/;
const estimate = "Re-run #4A · ≈ $0.12 · about 1 min";

// Saves that start runs while an evaluation is edited or added and another
// run is kept: each scores the kept runs too.
const scoresKept = {
  "an evaluation edited with a re-run prompt and only prompts kept": {
    evalsEdited: ["Tone"],
    rerun: ["#4A"],
    kept: ["#4B"],
  },
  "an evaluation edited with a re-run agent": {
    evalsEdited: ["Tone"],
    rerun: ["#4C"],
    rerunAgents: ["#4C"],
    kept: ["#4A", "#4B"],
  },
  "an evaluation added with a re-run run": {
    evalsAdded: ["Brevity"],
    rerun: ["#4A"],
    kept: ["#4B"],
  },
  "an evaluation edited with a new run": {
    evalsEdited: ["Tone"],
    added: ["#4C"],
    kept: ["#4A", "#4B"],
  },
  "an evaluation added with a new run": {
    evalsAdded: ["Brevity"],
    added: ["#4C"],
    kept: ["#4A", "#4B"],
  },
  "an evaluation edited and another added with a re-run run": {
    evalsEdited: ["Tone"],
    evalsAdded: ["Brevity"],
    rerun: ["#4A"],
    kept: ["#4B"],
  },
};
const cases = Object.entries(scoresKept);
const mixed = {
  evalsEdited: ["Tone"],
  rerun: ["#4A"],
  kept: ["#4B", "#4C"],
  keptAgents: ["#4C"],
};

// The footer text of a save, chosen as the footer chooses it.
function chipOf(change, { estimate: shown = null } = {}) {
  return editChipText(editSaveKind(change), {
    estimate: shown,
    scoresKeptRuns: editScoresKeptRuns(change),
  });
}

describe("editSaveKind", () => {
  it("names the four kinds of save", () => {
    expect(editSaveKind({ evalsEdited: ["Tone"] })).toBe("evaluation");
    expect(editSaveKind(mixed)).toBe("mixed");
    expect(editSaveKind({ evalsAdded: ["Brevity"] })).toBe("added");
    const both = { evalsEdited: ["Tone"], evalsAdded: ["Brevity"] };
    expect(editSaveKind(both)).toBe("editedAndAdded");
  });

  it("names no kind for a save no sentence covers", () => {
    // A prompt-only save, and nothing changed.
    expect(editSaveKind({ rerun: ["#4A"] })).toBeNull();
    expect(editSaveKind({})).toBeNull();
    // An evaluation edited and another added, with a re-run run.
    const bothAndRerun = {
      evalsEdited: ["Tone"],
      evalsAdded: ["Brevity"],
      rerun: ["#4A"],
    };
    expect(editSaveKind(bothAndRerun)).toBeNull();
    // An evaluation added together with a re-run run.
    const addAndRerun = { evalsAdded: ["Brevity"], rerun: ["#4A"] };
    expect(editSaveKind(addAndRerun)).toBeNull();
    // An evaluation edited with a re-run run and no kept agent run.
    const noKeptAgent = { evalsEdited: ["Tone"], rerun: ["#4A"] };
    expect(editSaveKind(noKeptAgent)).toBeNull();
    // An evaluation edited with a re-run agent run.
    const agentRerun = {
      evalsEdited: ["Tone"],
      rerun: ["#4C"],
      rerunAgents: ["#4C"],
      keptAgents: ["#4D"],
    };
    expect(editSaveKind(agentRerun)).toBeNull();
    // Any save that adds a run.
    const editAndNewRun = { evalsEdited: ["Tone"], added: ["#4C"] };
    const addAndNewRun = { evalsAdded: ["Brevity"], added: ["#4C"] };
    const bothAndNewRun = {
      evalsEdited: ["Tone"],
      evalsAdded: ["Brevity"],
      added: ["#4C"],
    };
    expect(editSaveKind(editAndNewRun)).toBeNull();
    expect(editSaveKind(addAndNewRun)).toBeNull();
    expect(editSaveKind(bothAndNewRun)).toBeNull();
  });
});

describe("editScoresKeptRuns", () => {
  it.each(cases)("is true for %s", (_name, change) => {
    expect(editScoresKeptRuns(change)).toBe(true);
  });

  it("is true for a mixed save", () => {
    expect(editScoresKeptRuns(mixed)).toBe(true);
  });

  it("is false when no run starts, no score changes or no run is kept", () => {
    // Evaluations edited, added or both, with no run started.
    const edited = { evalsEdited: ["Tone"], kept: ["#4A"] };
    const added = { evalsAdded: ["Brevity"], kept: ["#4A"] };
    const both = { ...edited, ...added };
    // A re-run with no evaluation edited or added.
    const rerunOnly = { rerun: ["#4A"], kept: ["#4B"] };
    // A re-run of every run: nothing is kept.
    const nothingKept = { evalsEdited: ["Tone"], rerun: ["#4A", "#4B"] };
    for (const change of [edited, added, both, rerunOnly, nothingKept, {}]) {
      expect(editScoresKeptRuns(change)).toBe(false);
    }
  });
});

describe("editKeptLine", () => {
  it("says the edited evaluation is scored again on every run", () => {
    expect(editKeptLine([], runs, { kind: "evaluation" })).toBe(
      `Kept runs keep their outputs. ${EDITED}`,
    );
  });

  it("says a mixed save starts a new run of the changed prompt, then scores the edited evaluation again on every run", () => {
    // The save binds a new run beside #4A; the agent is kept.
    expect(editKeptLine(["#4A"], runs, { kind: "mixed" })).toBe(
      `A changed run starts a new run: #4A·2. #4A keeps its results. ${EDITED}`,
    );
  });

  it("says the edited and added evaluations are scored on every run", () => {
    expect(editKeptLine([], runs, { kind: "editedAndAdded" })).toBe(
      `Kept runs keep their outputs. ${EDITED_AND_ADDED}`,
    );
  });

  it("says nothing about cleared scores for any save", () => {
    const kinds = ["evaluation", "editedAndAdded", "mixed", "added", null];
    for (const kind of kinds) {
      expect(editKeptLine(["#4A"], runs, { kind })).not.toMatch(RETIRED);
    }
  });

  it("says the added evaluation is scored on every run", () => {
    expect(editKeptLine([], runs, { kind: "added" })).toBe(
      `Kept runs keep their outputs. ${ADDED}`,
    );
  });

  it("keeps today's line when no evaluation is edited or added", () => {
    expect(editKeptLine([], runs)).toBe(
      "Results of kept runs stay as they are.",
    );
    expect(editKeptLine([], runs, { kind: null })).toBe(
      "Results of kept runs stay as they are.",
    );
  });

  it("leaves a prompt-only save's line as it was", () => {
    expect(editKeptLine(["#4A"], runs)).toBe(
      "A changed run starts a new run: #4A·2. #4A keeps its results.",
    );
  });
});

describe("editChipText", () => {
  it("gives a save that starts no run its sentence, with no figure", () => {
    const both = { evalsEdited: ["Tone"], evalsAdded: ["Brevity"] };
    expect(editChipText("evaluation", { estimate })).toBe(EDITED);
    expect(editChipText("added", { estimate })).toBe(ADDED);
    expect(chipOf(both, { estimate })).toBe(EDITED_AND_ADDED);
    expect([EDITED, ADDED, EDITED_AND_ADDED].join(" ")).not.toMatch(/\$/);
  });

  it("gives a mixed save the edited-evaluation sentence, then the re-run estimate", () => {
    expect(chipOf(mixed, { estimate })).toBe(
      `${EDITED} ${estimate}. ${NOT_ESTIMATED}`,
    );
    expect(chipOf(mixed, { estimate })).not.toMatch(RETIRED);
  });

  it("gives a mixed save with no estimate its sentence alone", () => {
    expect(chipOf(mixed)).toBe(EDITED);
    expect(chipOf(mixed)).not.toMatch(FRAGMENT);
  });

  it.each(cases)("says what the amount leaves out: %s", (_name, change) => {
    expect(chipOf(change, { estimate })).toBe(`${estimate}. ${NOT_ESTIMATED}`);
  });

  it("adds no words beside an estimate with no money amount", () => {
    const unpriced = "Re-run #4A · ~$ — · ~1 min";
    expect(chipOf(mixed, { estimate: unpriced })).toBe(`${EDITED} ${unpriced}`);
    for (const change of Object.values(scoresKept)) {
      expect(chipOf(change, { estimate: unpriced })).toBe(unpriced);
    }
  });

  it("adds no words to a save with no estimate", () => {
    for (const change of Object.values(scoresKept)) {
      expect(chipOf(change)).toBeNull();
    }
  });

  it("keeps the estimate for any other save", () => {
    expect(editChipText(null)).toBeNull();
    expect(editChipText(null, { estimate })).toBeNull();
    // A re-run with no evaluation edited or added scores no kept run.
    expect(chipOf({ rerun: ["#4A"], kept: ["#4B"] }, { estimate })).toBeNull();
  });
});

// The confirm's second line, chosen as RunExperiment chooses it.
function confirmOf(change) {
  return editKeptLine(change.rerun ?? [], runs, {
    kind: editSaveKind(change),
    keptScoring: editKeptScoring(change),
  });
}

const KEPT = "Kept runs keep their outputs.";
const RERUN_4A =
  "A changed run starts a new run: #4A·2. #4A keeps its results.";
const RERUN_4C =
  "A changed run starts a new run: #4C·2. #4C keeps its results.";
// Each save that scores kept runs, in the order of `cases`: the re-run line,
// or `Kept runs keep their outputs.` when no run re-runs, then the approved
// sentence for what changed.
const CONFIRM_LINES = [
  `${RERUN_4A} ${EDITED}`,
  `${RERUN_4C} ${EDITED}`,
  `${RERUN_4A} ${ADDED}`,
  `${KEPT} ${EDITED}`,
  `${KEPT} ${ADDED}`,
  `${RERUN_4A} ${EDITED_AND_ADDED}`,
];
const confirmCases = cases.map(([name, change], i) => [
  name,
  change,
  CONFIRM_LINES[i],
]);

describe("editKeptScoring", () => {
  it("names what changed for each save that scores kept runs", () => {
    const keys = cases.map(([, change]) => editKeptScoring(change));
    expect(keys).toEqual([
      "evaluation",
      "evaluation",
      "added",
      "evaluation",
      "added",
      "editedAndAdded",
    ]);
  });

  it("is null for a save that scores no kept run", () => {
    // No run starts, no evaluation changes, or nothing is kept.
    const edited = { evalsEdited: ["Tone"], kept: ["#4A"] };
    const rerunOnly = { rerun: ["#4A"], kept: ["#4B"] };
    const nothingKept = { evalsEdited: ["Tone"], rerun: ["#4A", "#4B"] };
    for (const change of [edited, rerunOnly, nothingKept, {}]) {
      expect(editKeptScoring(change)).toBeNull();
    }
  });
});

describe("the confirm line of a save that also scores kept runs", () => {
  it.each(confirmCases)("%s", (_name, change, line) => {
    expect(confirmOf(change)).toBe(line);
    expect(confirmOf(change)).not.toMatch(RETIRED);
  });

  it("an evaluation edited and another added with a new run", () => {
    const change = {
      evalsEdited: ["Tone"],
      evalsAdded: ["Brevity"],
      added: ["#4C"],
      kept: ["#4A", "#4B"],
    };
    expect(confirmOf(change)).toBe(`${KEPT} ${EDITED_AND_ADDED}`);
  });

  it("gives a mixed save the re-run line, then its sentence", () => {
    expect(confirmOf(mixed)).toBe(`${RERUN_4A} ${EDITED}`);
  });

  it("leaves the lines of the other named kinds as they were", () => {
    const both = { evalsEdited: ["Tone"], evalsAdded: ["Brevity"] };
    expect(confirmOf({ evalsEdited: ["Tone"] })).toBe(`${KEPT} ${EDITED}`);
    expect(confirmOf({ evalsAdded: ["Brevity"] })).toBe(`${KEPT} ${ADDED}`);
    expect(confirmOf(both)).toBe(`${KEPT} ${EDITED_AND_ADDED}`);
  });

  it("keeps today's line for a save that scores no kept run", () => {
    // A new run with no evaluation edited or added.
    expect(confirmOf({ added: ["#4C"], kept: ["#4A", "#4B"] })).toBe(
      "Results of kept runs stay as they are.",
    );
    // A re-run with no evaluation edited or added.
    expect(confirmOf({ rerun: ["#4A"], kept: ["#4B"] })).toBe(RERUN_4A);
    // An evaluation edited with every run re-run: nothing is kept.
    const allRerun = { evalsEdited: ["Tone"], rerun: ["#4A", "#4B"] };
    expect(confirmOf(allRerun)).toBe(
      "Changed runs start new runs: #4A·2 and #4B·2. #4A and #4B keep their results.",
    );
  });
});
