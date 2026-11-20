/*
  Scenarios an improvement created.

  An improvement (a Fix from the Error Feed, a scheduled run) can write
  scenarios of its own: the ones that reproduce what it is fixing. They stay
  with the improvement — a Fix's scenarios included — until a person adds
  them — from the Scenarios tab ("N scenarios from improvements aren't in this
  environment") or from the improvement's own page. Both write the same way,
  through `addImprovementScenariosPatch`, so a scenario added from either
  place reads the same: a scenario in the environment, with its source badge.
  Choosing them there is the review — they join at once.

  Read from the improvement record, whichever field it keeps them on:
    record.issueScenarios   rows the improvement wrote (preferred)
    record.createdScenarios the same, under its older name
  A row already in envState.scenarios is in the environment and isn't listed.
*/
import { stampProvenance, defaultBatchId, currentUser } from "./scenarioProvenance";
import { suiteLogPatch } from "./suiteLog";

/** The rows an improvement created. */
export const improvementScenarioRows = (record) => {
  const raw = record?.issueScenarios || record?.createdScenarios || [];
  return (Array.isArray(raw) ? raw : []).filter((r) => r && typeof r === "object" && r.id);
};

/** Its rows that aren't in this environment yet. */
export const improvementScenariosNotIn = (envState, record) => {
  const inEnv = new Set((envState?.scenarios || []).map((s) => s.id));
  return improvementScenarioRows(record).filter((r) => !inEnv.has(r.id));
};

/**
 * Every improvement on this environment with scenarios the environment
 * doesn't have, newest first: [{ record, rows }].
 */
export const pendingImprovementScenarios = (envState) => (envState?.optimizations || [])
  .map((record) => ({ record, rows: improvementScenariosNotIn(envState, record) }))
  .filter((g) => g.rows.length > 0)
  .sort((a, b) => (Date.parse(b.record.createdAt) || 0) - (Date.parse(a.record.createdAt) || 0));

export const pendingImprovementCount = (envState) => pendingImprovementScenarios(envState).reduce((n, g) => n + g.rows.length, 0);

const nameOf = (record) => record?.name || record?.trigger?.issueTitle || record?.id || "an epoch";

/**
 * The envState patch that adds some of an improvement's scenarios to the
 * environment. `ids` picks which (all of them when omitted). Picking them is
 * the review, so each joins the environment at once, keeps the source badge
 * it had (an Error Feed issue, a failing case) or reads
 * "From an improvement", and remembers which improvement made it. The
 * scenario set moves to a new version with the summary saying so.
 *
 * Returns {} when there is nothing to add.
 */
export const addImprovementScenariosPatch = (envState, record, ids, { by = currentUser().name, at = new Date().toISOString() } = {}) => {
  const want = ids ? new Set(ids) : null;
  const rows = improvementScenariosNotIn(envState, record).filter((r) => !want || want.has(r.id));
  if (!rows.length) return {};
  const batchId = defaultBatchId("improvement", at);
  const added = rows.map((r) => {
    const source = r.source || (r.errorFeedIssue ? "error-feed" : r.failingCase ? "failing-case" : "improvement");
    const { proposed: _p, tier: _t, ...row } = r;
    return {
      ...stampProvenance(row, { source, at, batchId }),
      fromImprovement: { id: record.id, name: nameOf(record) },
      batchNote: r.batchNote || `from ${nameOf(record)}`,
    };
  });
  return suiteLogPatch(envState, {
    scenarios: [...(envState?.scenarios || []), ...added],
  }, {
    summary: `Added ${added.length} scenario${added.length === 1 ? "" : "s"} from ${nameOf(record)}`,
    by,
  });
};
