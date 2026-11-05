import { describe, expect, it } from "vitest";
import {
  GRADING_ERROR_REASON,
  denominatorLine,
  evaluationCountsOf,
  gradingErrorKeys,
  missingFieldsOf,
  rangesOf,
  runEvaluation,
  scopeTrialsOf,
  wrapTrials,
} from "../evalEvidence";
import { runAsk } from "../trialQuestions";
import { answerAsk } from "../runInsightReports";
import { VERDICT_CAUSES } from "../decoupled";

const task = (i, status = "failed", extra = {}) => ({
  id: `t${i}`,
  title: `Scenario ${i}`,
  status,
  steps: [{ role: "agent" }],
  callLog: [{ tool: "x" }],
  ...extra,
});
const tasks = Array.from({ length: 16 }, (_, i) =>
  task(i + 1, i % 3 === 0 ? "passed" : "failed"),
);
const ctx = {
  runId: "r2",
  runLabel: "Run 2",
  tasks,
  others: [{ id: "r1", label: "Run 1", tasks: tasks.slice(0, 5) }],
  voice: true,
  worldHeld: true,
};

describe("evaluating saved evidence", () => {
  it("names the field a trial is missing, per trial", () => {
    const [t14] = wrapTrials("r2", "Run 2", tasks, "r2").filter(
      (t) => t.trialNo === 14,
    );
    const lacks = missingFieldsOf(t14, ["transcript"], ctx);
    expect(lacks[0].text).toBe("no recording for Transcript");
    expect(missingFieldsOf(t14, ["goal"], ctx)).toEqual([]);
  });

  it("scopes to a filter snapshot, a selection or other runs", () => {
    const filter = { name: "Failed · Goal: refund", taskIds: ["t2", "t3"] };
    expect(scopeTrialsOf({ scope: "filter", ctx, filter })).toHaveLength(2);
    expect(
      scopeTrialsOf({ scope: "selected", ctx, selectedIds: ["t5"] }),
    ).toHaveLength(1);
    const two = scopeTrialsOf({ scope: "all", ctx, runIds: ["r1"] });
    expect(two).toHaveLength(21);
    expect(new Set(two.map((t) => t.id)).size).toBe(21);
  });

  it("keeps skipped and grading errors apart from scores", () => {
    const trials = scopeTrialsOf({ scope: "all", ctx });
    const r = runEvaluation({
      evaluation: { id: "tone", name: "Tone", version: "v2", threshold: 0.75 },
      mapping: { output: "transcript" },
      trials,
      ctx,
      scope: "all",
      scopeLabel: "All trials",
    });
    const c = evaluationCountsOf(r);
    expect(r.id).toMatch(/^EVR-[0-9A-Z]{4}$/);
    expect(c.requested).toBe(16);
    expect(c.skipped).toBe(2);
    expect(c.errors).toBeGreaterThanOrEqual(1);
    expect(c.evaluated + c.skipped + c.errors).toBe(16);
    expect(c.passed + c.failed).toBe(c.evaluated);
    expect(Object.keys(r.skipped)).toEqual(["t7", "t14"]);
    expect(denominatorLine(c)).toContain("evaluated of 16 requested");
    expect(r.evidence.runs[0].trials).toHaveLength(16);
  });

  it("always has a grading error when four or more trials are read", () => {
    const keys = ["a", "b", "c", "d", "e"];
    expect(gradingErrorKeys(keys, "s").size).toBeGreaterThanOrEqual(1);
    expect(gradingErrorKeys(keys, "s")).toEqual(gradingErrorKeys(keys, "s"));
  });

  it("an Instant eval ask never counts a grading error as an answer", () => {
    const trials = scopeTrialsOf({ scope: "all", ctx });
    const ask = runAsk({
      questions: [{ text: "Clear?", type: "yes_no" }],
      reads: "conversation",
      scope: "all",
      scopeLabel: "All trials",
      trials,
      ctx,
    });
    const answers = Object.values(ask.answers.q1);
    const errors = answers.filter((a) => a.error);
    expect(errors.length).toBeGreaterThanOrEqual(1);
    expect(errors[0].reason).toBe(GRADING_ERROR_REASON);
    expect(errors[0].answer).toBeNull();
    expect(ask.evaluator).toEqual({ name: "Instant eval", version: "v1" });
  });

  it("writes trial number ranges", () => {
    expect(rangesOf([1, 2, 3, 5, 7, 8])).toBe("1–3, 5, 7–8");
  });
});

describe("insight follow-ups", () => {
  const report = {
    categories: [{ ids: ["t2", "t3"] }],
    findings: [],
  };
  it("says there is too little evidence under three trials", () => {
    const a = answerAsk(report, "Which personas are hit hardest?", tasks);
    expect(a.tooLittle).toMatch(/Too little evidence/);
    expect(a.observed).toEqual([]);
  });
});

describe("cause labels", () => {
  it("use the doc's words", () => {
    expect(VERDICT_CAUSES["inc-evidence"].label).toBe("Unmeasured · evidence");
    expect(VERDICT_CAUSES["inc-judge"].label).toBe("Grading error · judge");
  });
});
