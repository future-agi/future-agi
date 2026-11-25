/*
  The scenarios table's "Last 3 runs" column, as plain data.

  A scenario is passed, flaky (some trials passed, some failed) or failed in
  each run it was in. The column shows the last three of those, oldest first.
  A run that played an earlier, not-comparable version of the scenario is
  shown blank — its result says nothing about the scenario as it reads now.
*/

import { scenarioVersionOf } from "./scenarioVersion";
import { playedVersionOf } from "./decoupled";

export const HISTORY_DOTS = 3;

/*
  THE one place that decides whether a past run's result still speaks for the
  scenario as it is now. Today: the same version only. To loosen it later (for
  instance "comparable when the two versions are about 75% similar"), change
  this function and nothing else — the table only calls it.
*/
export const isComparableVersion = (current, ranOn) =>
  !ranOn || !current || String(current) === String(ranOn);

const SCORED = new Set(["passed", "flaky", "failed"]);

/* "2 of 3 trials" — only when the run had trials to count. */
const trialCountOf = (h) => {
  const samples = (h.samples || []).filter(Boolean);
  if (samples.length < 2) return null;
  const passed = samples.filter((s) => s === "passed").length;
  return `${passed} of ${samples.length} trials`;
};

/* A run's result speaks for the current agent only when it ran on the same
   agent and environment version. A missing stamp is given the benefit. */
export const isOtherAgent = (current, h) =>
  !!(
    current &&
    ((current.agent && h.agentVersion && current.agent !== h.agentVersion) ||
      (current.env && h.envVersion && current.env !== h.envVersion))
  );

export const dotTitleOf = (dot, current = null, envLabel = (v) => v) => {
  if (!dot.comparable) return "Ran on an earlier version of this scenario";
  const word =
    dot.status === "flaky" && dot.trials ? `flaky (${dot.trials})` : dot.status;
  const ran = [
    dot.agentVersion && `agent ${dot.agentVersion}`,
    dot.envVersion && `env ${envLabel(dot.envVersion)}`,
  ].filter(Boolean);
  const head = [dot.name, ...ran, word].join(" · ");
  return dot.otherAgent && current?.agent
    ? `${head} — not the current agent (${current.agent})`
    : head;
};

/**
 * The dots for one scenario: its last `limit` scored runs, oldest first.
 * `history` is the table's per-run list for the scenario (a run it wasn't in
 * has no status); `currentVersion` is the scenario's version now; `current`
 * = { agent, env } is the environment's agent/env version now (a dot from
 * another one is `otherAgent`). Never run: an empty list.
 */
export const recentDotsOf = (
  history,
  currentVersion,
  limit = HISTORY_DOTS,
  current = null,
  envLabel = (v) => v,
) =>
  (history || [])
    .filter((h) => SCORED.has(h.status))
    .slice(-limit)
    .map((h) => {
      const dot = {
        runId: h.runId,
        name: h.name,
        status: h.status,
        trials: trialCountOf(h),
        ranVersion: h.ranVersion || null,
        agentVersion: h.agentVersion || null,
        envVersion: h.envVersion || null,
        comparable: isComparableVersion(currentVersion, h.ranVersion),
        otherAgent: isOtherAgent(current, h),
      };
      return { ...dot, title: dotTitleOf(dot, current, envLabel) };
    });

/* The version of `row` a run played — for the per-run history entries. */
export const ranVersionOf = (run, row) => playedVersionOf(run, row);
export const currentVersionOf = (row) => scenarioVersionOf(row);

/* True when at least one of the rows has a scored run — else the column is left out. */
export const anyScenarioRan = (rows, historyOf) =>
  (rows || []).some((r) =>
    ((historyOf && historyOf(r)) || []).some((h) => SCORED.has(h.status)),
  );
