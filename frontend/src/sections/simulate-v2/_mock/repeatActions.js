/*
  One Repeat chooser (roadmap runs.md "Replay and rerun choices"): every way
  to repeat a run, each saying whether it makes new agent calls, what it
  does to state and what it costs. Unsupported ones stay listed, disabled
  with the reason — a restart from the seed is never called a restore.
*/
import { estimateRunPlan, rangeLabel } from "./pricing";
import { takesSnapshot } from "./candidateSource";
import { resolveEval } from "./evals";
import { endStateSavedOf } from "./regrade";
import { retriedIdsOf, attemptsOf } from "./runPlanLink";
import { SHOW_RUN_COST } from "./costFlags";

/* " · ≈ N credits" — empty while pre-run cost is off (costFlags). */
const costTail = (...a) => {
  const c = costOf(...a);
  return c ? ` · ${c}` : "";
};
const costOf = (env, envState, run, n) => {
  if (!SHOW_RUN_COST) return null;
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
  return e.platform.unknown
    ? "can't estimate"
    : `≈ ${rangeLabel(e.platform.low, e.platform.high)}`;
};

/**
 * The chooser's rows beyond the two re-run modes, by id:
 * { replay, reeval, failures, recorded, resume, checkpoint, fork } —
 * each { label, detail, meta, disabled? }.
 */
export const repeatActionsOf = ({
  env,
  envState,
  run,
  tasks = [],
  regrade = null,
}) => {
  const failing = [
    ...new Set(
      tasks
        .filter((t) => t.status === "failed" || t.status === "flaky")
        .map((t) => t.scenarioId || t.id),
    ),
  ];
  const notCounted = tasks.filter((t) => t.status === "unmeasured").length;
  /* Ids a linked attempt already took over aren't offered again. */
  const retried = retriedIdsOf(run);
  const notStarted = (run?.budget?.notStarted || []).filter(
    (id) => !retried.includes(id),
  );
  const alreadyRetried = !notStarted.length && retried.length > 0;
  const stateSaved = endStateSavedOf({ env, envState, run });
  const total = run?.scenarioIds?.length || tasks.length;
  const agentName = envState?.agent?.name || "This agent";
  const code = takesSnapshot(envState?.agent);
  const noCheckpoint = code
    ? "This run saved no checkpoints; a run restarts unfinished trials from their seed instead."
    : `${agentName} runs on its own platform, so its conversation memory can't be restored. Resume interrupted work restarts unfinished trials from their seed — a restart, not a restore.`;
  return {
    replay: {
      label: "View saved evidence",
      detail:
        "Play the recorded conversations, tool calls and state changes. Nothing runs again.",
      meta: "No new agent calls · free",
      disabled: !tasks.length ? "Nothing was recorded." : null,
    },
    reeval: {
      label: "Re-evaluate",
      detail: `${
        regrade
          ? `Read the stored traces again with the repaired test (${regrade.label}).`
          : stateSaved
            ? "Apply another evaluation to the stored traces and end states."
            : "Apply another evaluation to the stored traces."
      } The agent's run stays as it was, and the previous result is kept beside the new one.${stateSaved ? "" : " End state wasn't saved — only the conversation checks are re-graded."}`,
      meta: `No new agent calls · ${SHOW_RUN_COST && regrade ? `≈ ${regrade.credits.toLocaleString()} credits` : "judges only"}`,
    },
    failures: {
      label: `Re-run ${failing.length} failing`,
      detail: `A new run of the scenarios that failed here, resolved from this run.${notCounted ? ` The ${notCounted} that weren't counted (inconclusive, test invalid or error) can be added in the run plan.` : ""}`,
      meta: `New agent calls · world reset${costTail(env, envState, run, failing.length)}`,
      disabled: !failing.length ? "Nothing failed in this run." : null,
      ids: failing,
    },
    recorded: {
      label: "Recorded-tool replay",
      detail:
        "Run the agent again, with every tool answering what it returned in this run. It can't show how the agent does against a world that changes.",
      meta: `New agent calls · tools from the recording${costTail(env, envState, run, total)}`,
      disabled: "Coming later.",
    },
    resume: {
      label: notStarted.length
        ? `Retry ${notStarted.length} interrupted`
        : "Retry interrupted trials",
      detail: notStarted.length
        ? `Continue under this run's saved configuration. The ${Math.max(0, total - notStarted.length)} that finished aren't repeated or charged again; unfinished trials restart from their seed.`
        : "Continue unfinished trials under the saved configuration.",
      meta: notStarted.length
        ? `New agent calls${costTail(env, envState, run, notStarted.length)}`
        : null,
      disabled: notStarted.length
        ? null
        : alreadyRetried
          ? `Already retried as attempt ${attemptsOf(run).slice(-1)[0].attempt}.`
          : "Every trial in this run finished — there's nothing to resume.",
      ids: notStarted,
    },
    checkpoint: {
      label: "Resume from checkpoint",
      detail:
        "Restore the agent and world as they were at a saved point and carry on.",
      meta: null,
      disabled: noCheckpoint,
    },
    fork: {
      label: "Fork from checkpoint",
      detail:
        "Branch from a saved point with a changed prompt, model or code; the original stays as it is.",
      meta: null,
      disabled: noCheckpoint,
    },
  };
};
