/*
  What a trial's evidence holds (roadmap runs.md "Live run and trial
  detail"): each artifact the adapter provides, and the ones it doesn't —
  labelled missing with why, never silently absent. A failed check names
  its source rule and the scenario version it ran. A dispute is a note on
  the result; the recorded score is never rewritten.
*/
import { rulesOf, ruleOfScenario } from "./envModel";
import { scenarioVersionOf } from "./scenarioVersion";
import { takesSnapshot } from "./candidateSource";
import { currentUser } from "./scenarioProvenance";
import { traceOf } from "./runTrace";

/** [{ id, label, recorded, note, pane }] — `pane` is the drawer tab that shows it. */
export const trialArtifactsOf = ({ task, env, envState, stage, conversational, voice }) => {
  const steps = (task?.steps || []).length;
  const tools = traceOf(task, env, envState).spans.filter((s) => s.kind === "tool").length;
  const agentName = envState?.agent?.name || "This agent";
  return [
    voice && { id: "recording", label: "Call recording", recorded: steps > 0, note: steps ? "Both sides, as heard" : "The call never connected", pane: "transcript" },
    conversational && { id: "transcript", label: "Transcript", recorded: steps > 0, note: steps ? `${steps} turns` : "No turns were recorded", pane: "transcript" },
    !conversational && { id: "actions", label: "Actions", recorded: steps > 0, note: steps ? `${steps} actions` : "No actions were recorded", pane: "transcript" },
    stage === "browser" && { id: "screens", label: "Screenshots", recorded: steps > 0, note: "One per action", pane: "transcript" },
    { id: "trace", label: "Trace", recorded: steps > 0, note: steps ? "LLM, tool and guard spans on one time axis" : "This scenario recorded no steps", pane: "trace" },
    { id: "tools", label: "Tool calls", recorded: true, note: tools ? `${tools} calls with what was sent and returned` : "None made", pane: "trace" },
    {
      id: "state",
      label: "State changes",
      recorded: !!envState?.twinBacking,
      note: envState?.twinBacking
        ? "Before and after each write"
        : "Not recorded — this environment isn't backed by a cloned service, so state changes aren't captured. Tool calls in Trace show what was sent.",
      pane: "twin",
    },
    takesSnapshot(envState?.agent)
      ? { id: "files", label: "Files the agent produced", recorded: true, note: "None produced in this trial", pane: null }
      : { id: "files", label: "Files the agent produced", recorded: false, note: `Not provided — ${agentName} runs on its own platform and doesn't send files back`, pane: null },
  ].filter(Boolean);
};

/** Where a failed check comes from: its rule (when the scenario tests one) and the scenario version it ran. */
export const checkSourceOf = (task, env, envState, run = null) => {
  const scenario = (envState?.scenarios || []).find((s) => s.id === task?.id) || task;
  const rule = scenario ? ruleOfScenario(scenario, rulesOf(env, envState)) : null;
  const version = run?.scenarioVersions?.[task?.id] || (scenario ? scenarioVersionOf(scenario) : null);
  return { rule, version };
};

const disputeKey = (runId, taskId) => `${runId || "run"}:${taskId}`;

export const disputesOf = (envState, runId, taskId) => envState?.disputes?.[disputeKey(runId, taskId)] || [];

/** The patch that adds a dispute note — the score it disputes stays as recorded. */
export const addDisputePatch = (envState, runId, taskId, note, score) => ({
  disputes: {
    ...(envState?.disputes || {}),
    [disputeKey(runId, taskId)]: [
      ...disputesOf(envState, runId, taskId),
      { note, by: currentUser().name, at: new Date().toISOString(), score },
    ],
  },
});
