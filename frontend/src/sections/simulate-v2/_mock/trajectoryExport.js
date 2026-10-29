/*
  Export a run's trajectories (roadmap runs.md "Results, analytics and
  durability"): the resolved configuration, each trial's outcome and the
  evidence it's allowed to carry. Hidden evaluation material — reference
  paths, judge instructions, held-back cases — stays out.
*/
import { causeOf, outcomeOfCause } from "./decoupled";

export const trajectoryExportOf = ({ env, run, tasks = [] }) => ({
  environment: { id: env?.id, name: env?.name, version: run?.envVersion || null },
  run: {
    id: run?.id,
    ordinal: run?.ordinal ?? null,
    agentVersion: run?.agentVersion || null,
    seed: run?.seed || run?.id || null,
    repeats: run?.repeats || 1,
    limits: run?.limits || null,
    scenarioVersions: run?.scenarioVersions || null,
    evaluations: (run?.evals || []).map((e) => e?.name || e?.id || e).filter(Boolean),
    ...(run?.toolReplayOf && { recordedToolReplayOf: run.toolReplayOf }),
    ...(run?.snapshotOf && { snapshotOf: run.snapshotOf }),
  },
  trials: tasks
    .filter((t) => !t.heldBack)
    .map((t) => ({
      scenarioId: t.scenarioId || t.id,
      title: t.title || t.name,
      status: t.status,
      outcome: t.status === "unmeasured" ? outcomeOfCause(causeOf(t)) : t.status,
      passes: t.passes ?? null,
      repeats: t.repeats ?? null,
      steps: (t.steps || []).map((s) => ({
        role: s.role,
        text: s.text,
        ...(s.tool && { tool: s.tool }),
        ...(s.actorName && { actor: s.actorName }),
      })),
      evaluations: (t.evalResults || []).map((r) => ({ name: r.name, score: r.score ?? null, passed: !!r.passed })),
    })),
  left_out: "Hidden evaluation material (reference paths, judge instructions, held-back cases) isn't exported.",
});

/** Save `payload` as a JSON file. */
export const downloadJson = (payload, name) => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
