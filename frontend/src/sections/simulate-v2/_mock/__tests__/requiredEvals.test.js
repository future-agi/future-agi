import { describe, expect, it } from "vitest";
import { seededState } from "../seedState";
import { getEnvironment } from "../environments";
import { rebuildRun } from "../comparison";
import { rollupLine, subGoalResultsOf } from "../scenarioSubGoals";

/*
  Option B (Chintan via Dileep, 24 Nov): under the sub-goal rule a required
  evaluation that fails fails the conversation, even when every critical
  sub-goal held. Other evaluations are reported and never change it.
*/
const seed = seededState();
const env = getEnvironment("env-voice-support");
const base = seed.byEnv["env-voice-support"];
const run = { ...base.runs[0], gradingRule: 2, requiredEvals: 1 };
const idOf = (e) => (typeof e === "string" ? e : e.id);
const markAll = (required) => ({
  ...base,
  evals: (base.evals || []).map((e) => ({ id: idOf(e), required })),
});

describe("a required evaluation under the sub-goal rule", () => {
  /* The run pins its evaluations, so the flag goes on the run's copy too. */
  const plainState = markAll(false);
  const reqState = markAll(true);
  const plain = rebuildRun(env, plainState, {
    ...run,
    evals: plainState.evals,
  });
  const req = rebuildRun(env, reqState, { ...run, evals: reqState.evals });

  /* Not passed: failed, or flaky when a later repeat came out the other way. */
  it("fails a conversation whose sub-goals all held", () => {
    const flipped = req.filter(
      (t, i) => t.status !== "passed" && plain[i].status === "passed",
    );
    expect(flipped.length).toBeGreaterThan(0);
    flipped.forEach((t) => {
      expect(t.failedBy).toBe("required-evaluation");
      expect(t.requiredFail?.name).toBeTruthy();
    });
  });

  it("says it failed only because of the required evaluation", () => {
    const t = req.find((x) => x.failedBy === "required-evaluation");
    const line = rollupLine(subGoalResultsOf(t, env, reqState).rollup);
    expect(line).toMatch(
      /^Failed — required evaluation .+ failed; every critical sub-goal held$/,
    );
  });

  it("leaves results alone when no evaluation is required", () => {
    expect(plain.some((t) => t.failedBy === "required-evaluation")).toBe(false);
  });
});
