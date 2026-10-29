/*
  One Repeat chooser (roadmap runs.md "Replay and rerun choices"): every way
  to repeat a run, each saying whether it makes new agent calls, what it
  does to state and what it costs. Unsupported ones stay listed, disabled
  with the reason — a restart from the seed is never called a restore.
*/
import { estimateRunPlan, rangeLabel } from "./pricing";
import { takesSnapshot } from "./candidateSource";
import { resolveEval } from "./evals";

const costOf = (env, envState, run, n) => {
  if (!n) return null;
  const e = estimateRunPlan({
    env,
    envState,
    scenarios: n,
    attempts: run?.repeats || 3,
    versions: [run?.agentVersion].filter(Boolean),
    evals: (envState?.evals || []).map(resolveEval).filter(Boolean),
    runLocation: envState?.agent?.values?.runLocation || null,
  });
  return e.platform.unknown ? "can't estimate" : `≈ ${rangeLabel(e.platform.low, e.platform.high)}`;
};

/**
 * The chooser's rows beyond the two re-run modes, by id:
 * { replay, reeval, failures, recorded, resume, checkpoint, fork } —
 * each { label, detail, meta, disabled? }.
 */
export const repeatActionsOf = ({ env, envState, run, tasks = [], regrade = null }) => {
  const failing = [...new Set(tasks.filter((t) => t.status === "failed" || t.status === "flaky").map((t) => t.scenarioId || t.id))];
  const notCounted = tasks.filter((t) => t.status === "unmeasured").length;
  const notStarted = run?.budget?.notStarted || [];
  const total = run?.scenarioIds?.length || tasks.length;
  const agentName = envState?.agent?.name || "This agent";
  const code = takesSnapshot(envState?.agent);
  const noCheckpoint = code
    ? "This run saved no checkpoints. Long jobs like training save them; a run restarts unfinished trials from their seed instead."
    : `${agentName} runs on its own platform, so its conversation memory can't be restored. Resume interrupted work restarts unfinished trials from their seed — a restart, not a restore.`;
  return {
    replay: {
      label: "Replay evidence",
      detail: "Play the recorded conversations, tool calls and state changes. Nothing runs again.",
      meta: "No new agent calls · free",
      disabled: !tasks.length ? "Nothing was recorded." : null,
    },
    reeval: {
      label: "Re-evaluate evidence",
      detail: regrade
        ? `Read the stored traces again with the repaired test (${regrade.label}). The agent's run stays as it was.`
        : "Apply another evaluation to the stored traces and end states. The agent's run stays as it was.",
      meta: `No new agent calls · ${regrade ? `≈ ${regrade.credits.toLocaleString()} credits` : "judges only"}`,
    },
    failures: {
      label: `Re-run ${failing.length} failing`,
      detail: `A new run of the scenarios that failed here, resolved from this run.${notCounted ? ` The ${notCounted} that weren't counted (inconclusive, test invalid or error) can be added in the run plan.` : ""}`,
      meta: `New agent calls · world reset · ${costOf(env, envState, run, failing.length) || "—"}`,
      disabled: !failing.length ? "Nothing failed in this run." : null,
      ids: failing,
    },
    recorded: {
      label: "Recorded-tool replay",
      detail: "Run the agent again, with every tool answering what it returned in this run. It can't show how the agent does against a world that changes.",
      meta: `New agent calls · tools from the recording · ${costOf(env, envState, run, total) || "—"}`,
      disabled: !run?.finishedAt ? "Available once this run finishes and its tool responses are recorded." : null,
    },
    resume: {
      label: notStarted.length ? `Resume ${notStarted.length} unfinished` : "Resume interrupted work",
      detail: notStarted.length
        ? `Continue under this run's saved configuration. The ${Math.max(0, total - notStarted.length)} that finished aren't repeated or charged again; unfinished trials restart from their seed.`
        : "Continue unfinished trials under the saved configuration.",
      meta: notStarted.length ? `New agent calls · ${costOf(env, envState, run, notStarted.length) || "—"}` : null,
      disabled: notStarted.length ? null : "Every trial in this run finished — there's nothing to resume.",
      ids: notStarted,
    },
    checkpoint: {
      label: "Resume from checkpoint",
      detail: "Restore the agent and world as they were at a saved point and carry on.",
      meta: null,
      disabled: noCheckpoint,
    },
    fork: {
      label: "Fork from checkpoint",
      detail: "Branch from a saved point with a changed prompt, model or code; the original stays as it is.",
      meta: null,
      disabled: noCheckpoint,
    },
  };
};
