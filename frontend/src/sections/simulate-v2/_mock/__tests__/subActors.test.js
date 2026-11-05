import { describe, expect, it } from "vitest";
import { seededState } from "../seedState";
import { withFiveAGoal } from "../voiceScenarioTopUp";
import { getEnvironment } from "../environments";
import { rebuildRun } from "../comparison";
import { subGoalResultsOf } from "../scenarioSubGoals";
import { rollupOf } from "../envModel";
import { taskToVoiceData } from "../voiceCallData";
import {
  SIMULATED_USER_ONLY,
  callerCastOf,
  scenarioCountsByActor,
  scenarioMatchesSubActor,
  scenarioSubActorOptions,
  subActorsInTrial,
  subActorsOf,
  trialMatchesSubActor,
  trialSubActorNames,
  trialSubActorOptions,
  triggerLine,
  triggerOf,
  withLineOf,
} from "../subActors";

const seed = seededState();
const voiceEnv = getEnvironment("env-voice-support");
const voiceState = withFiveAGoal(voiceEnv, seed.byEnv["env-voice-support"]);
const travelEnv = getEnvironment("env-chat-travel");
const travelState = seed.byEnv["env-chat-travel"];

describe("trigger lines", () => {
  it("says when a sub-actor comes in", () => {
    expect(triggerLine({ arrival: "chance", chancePct: 30 })).toBe(
      "30% of calls",
    );
    expect(triggerLine({ arrival: "first-turn" })).toBe(
      "After the agent's first turn",
    );
    expect(triggerLine({ arrival: "answers" })).toBe("Answers the call");
  });

  it("only a phone has someone who answers it", () => {
    const actor = { id: "x", arrival: "answers" };
    expect(triggerOf(actor, null, true).arrival).toBe("answers");
    expect(triggerOf(actor, null, false).arrival).toBe("first-turn");
  });

  it("a scenario's own trigger wins over the actor's", () => {
    const actor = { id: "x", arrival: "first-turn" };
    const t = triggerOf(
      actor,
      { id: "x", trigger: { arrival: "chance", chancePct: 60 } },
      true,
    );
    expect(t).toEqual({ arrival: "chance", chancePct: 60 });
  });
});

describe("which scenarios include a sub-actor", () => {
  it("seeds a few on the phone line and on a chat environment", () => {
    [
      [voiceEnv, voiceState],
      [travelEnv, travelState],
    ].forEach(([env, es]) => {
      const callers = callerCastOf(env, es);
      const withOne = es.scenarios.filter(
        (s) => subActorsOf(s, callers, env).length,
      );
      expect(withOne.length).toBeGreaterThan(0);
      expect(withOne.length).toBeLessThan(es.scenarios.length / 2);
    });
  });

  it("is the same every time", () => {
    const callers = callerCastOf(voiceEnv, voiceState);
    const names = () =>
      voiceState.scenarios.map((s) =>
        subActorsOf(s, callers, voiceEnv)
          .map((x) => x.actor.id)
          .join(),
      );
    expect(names()).toEqual(names());
  });

  it("a scenario's own list wins, and an empty one means none", () => {
    const callers = callerCastOf(voiceEnv, voiceState);
    const row = voiceState.scenarios[0];
    expect(subActorsOf({ ...row, subActors: [] }, callers, voiceEnv)).toEqual(
      [],
    );
    const one = subActorsOf(
      { ...row, subActors: [{ id: "act-bystander" }] },
      callers,
      voiceEnv,
    );
    expect(one.map((x) => x.actor.id)).toEqual(["act-bystander"]);
    expect(withLineOf(one)).toBe("With Bystander");
    expect(withLineOf([])).toBeNull();
  });

  it("someone who left the cast is no longer in the scenario", () => {
    const row = { id: "s", subActors: [{ id: "act-gone" }] };
    expect(
      subActorsOf(row, callerCastOf(voiceEnv, voiceState), voiceEnv),
    ).toEqual([]);
  });
});

describe("the scenarios filter", () => {
  const callers = callerCastOf(voiceEnv, voiceState);

  it("lists each sub-actor with its count, then the simulated user alone", () => {
    const opts = scenarioSubActorOptions(
      voiceState.scenarios,
      callers,
      voiceEnv,
    );
    const last = opts[opts.length - 1];
    expect(last.id).toBe(SIMULATED_USER_ONLY);
    const total = opts.slice(0, -1).reduce((n, o) => n + o.count, 0);
    expect(total + last.count).toBe(voiceState.scenarios.length);
    expect(opts[0].label).toBe(`${opts[0].id} (${opts[0].count})`);
  });

  it("is not offered when no scenario includes one", () => {
    const none = voiceState.scenarios.map((s) => ({ ...s, subActors: [] }));
    expect(scenarioSubActorOptions(none, callers, voiceEnv)).toEqual([]);
  });

  it("matches by name, or by having none", () => {
    const withOne = voiceState.scenarios.find(
      (s) => subActorsOf(s, callers, voiceEnv).length,
    );
    const name = subActorsOf(withOne, callers, voiceEnv)[0].actor.name;
    expect(scenarioMatchesSubActor(withOne, [name], callers, voiceEnv)).toBe(
      true,
    );
    expect(
      scenarioMatchesSubActor(
        withOne,
        [SIMULATED_USER_ONLY],
        callers,
        voiceEnv,
      ),
    ).toBe(false);
    const bare = { ...withOne, subActors: [] };
    expect(
      scenarioMatchesSubActor(bare, [SIMULATED_USER_ONLY], callers, voiceEnv),
    ).toBe(true);
  });

  it("counts the scenarios that use each one for the cast", () => {
    const use = scenarioCountsByActor(voiceState.scenarios, callers, voiceEnv);
    const opts = scenarioSubActorOptions(
      voiceState.scenarios,
      callers,
      voiceEnv,
    );
    opts
      .filter((o) => o.id !== SIMULATED_USER_ONLY)
      .forEach((o) => {
        const actor = callers.find((c) => c.name === o.id);
        expect(use.get(actor.id)).toBe(o.count);
      });
  });
});

describe("a trial with a sub-actor", () => {
  const callers = callerCastOf(voiceEnv, voiceState);
  const tasks = rebuildRun(voiceEnv, voiceState, voiceState.runs[0]);
  const trials = tasks.map((task) => ({
    task,
    trial: subActorsInTrial({
      task,
      env: voiceEnv,
      envState: voiceState,
      callers,
    }),
  }));

  it("always reads the same", () => {
    const t = trials.find((x) => x.trial.joined.length);
    const again = subActorsInTrial({
      task: t.task,
      env: voiceEnv,
      envState: voiceState,
      callers,
    });
    expect(again.lines.map((l) => l.text)).toEqual(
      t.trial.lines.map((l) => l.text),
    );
  });

  it("has their lines in order, with a role and how they joined", () => {
    const t = trials.find((x) => x.trial.lines.length > 1);
    const after = t.trial.lines.map((l) => l.after);
    expect([...after].sort((a, b) => a - b)).toEqual(after);
    t.trial.lines.forEach((l) => {
      expect(l.role).toBeTruthy();
      expect(l.how).toMatch(/joined|answered/);
    });
  });

  it("counts for a sub-actor only when a line reached the agent", () => {
    const unheard = trials.find(
      (x) => x.trial.joined.length && !x.trial.joined.some((j) => j.reached),
    );
    const miss = trials.find((x) => x.trial.missed.length);
    [unheard, miss].filter(Boolean).forEach((x) => {
      expect(trialSubActorNames(x.trial)).toEqual([]);
    });
    const heard = trials.find((x) => trialSubActorNames(x.trial).length);
    expect(trialSubActorNames(heard.trial)).toEqual(
      heard.trial.joined.filter((j) => j.reached).map((j) => j.actor.name),
    );
  });

  it("filters a run's calls with counts that add up", () => {
    const names = trials.map((x) => trialSubActorNames(x.trial));
    const opts = trialSubActorOptions(names);
    const total = opts.reduce((n, o) => n + o.count, 0);
    expect(opts[opts.length - 1].id).toBe(SIMULATED_USER_ONLY);
    /* a call with two sub-actors would count twice; here each has at most one */
    expect(total).toBe(names.length);
    expect(trialMatchesSubActor([], [SIMULATED_USER_ONLY])).toBe(true);
    expect(trialMatchesSubActor(["A"], [SIMULATED_USER_ONLY])).toBe(false);
    expect(trialMatchesSubActor(["A"], ["A"])).toBe(true);
    expect(trialMatchesSubActor(["A"], [])).toBe(true);
  });
});

describe("grading a sub-goal about a sub-actor", () => {
  const callers = callerCastOf(voiceEnv, voiceState);
  const tasks = rebuildRun(voiceEnv, voiceState, voiceState.runs[0]);
  const rows = tasks
    .filter((t) => t.status !== "unmeasured")
    .map((task) => ({
      task,
      trial: subActorsInTrial({
        task,
        env: voiceEnv,
        envState: voiceState,
        callers,
      }),
      res: subGoalResultsOf(task, voiceEnv, voiceState),
    }));

  it("gets a verdict when they took part", () => {
    const x = rows.find((r) => r.trial.joined.length);
    const goal = x.res.items.find((g) => g.subActorId);
    expect(goal).toBeTruthy();
    expect(["met", "failed", "inconclusive"]).toContain(goal.status);
    expect(x.res.notApplicable).toEqual([]);
  });

  it("reads Not applicable when they did not come in, and stays out of the counts", () => {
    const x = rows.find((r) => r.trial.missed.length);
    expect(x).toBeTruthy();
    const na = x.res.notApplicable[0];
    expect(na.status).toBe("not-applicable");
    expect(x.res.items.some((g) => g.subActorId)).toBe(false);
    /* the score is over the graded sub-goals only */
    const withNa = rollupOf([...x.res.items, na]);
    expect(withNa.score).toBe(x.res.rollup.score);
  });
});

describe("the voice transcript", () => {
  it("labels a sub-actor's turn as not the person being served", () => {
    const callers = callerCastOf(voiceEnv, voiceState);
    const tasks = rebuildRun(voiceEnv, voiceState, voiceState.runs[0]);
    const task = tasks.find(
      (t) =>
        subActorsInTrial({
          task: t,
          env: voiceEnv,
          envState: voiceState,
          callers,
        }).lines.length,
    );
    const data = taskToVoiceData(task, { env: voiceEnv, envState: voiceState });
    const labelled = data.transcript.filter((t) => t.sub_actor);
    expect(labelled.length).toBeGreaterThan(0);
    expect(labelled[0].message).toContain("not the person being served");
    /* the caller and the agent still have all their turns */
    expect(
      data.transcript.filter((t) => !t.sub_actor && t.role !== "tool").length,
    ).toBe(task.steps.length);
  });
});
