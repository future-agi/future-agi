/*
  Make a copy (roadmap catalogue.md "Make a copy: duplicate, fork and
  scope"): same agent and channel makes a duplicate; another agent makes a
  fork that may need fitting. The source never changes. Old runs stay with
  the source, linked as evidence; new runs belong to the copy. What carries
  over and whether the copy compares with the source is said before it's
  made.
*/
import { groupsOf } from "./scenarioOrg";
import { envMustPassOf } from "./improvementStart";

/** The agent record an environment stores — a connected agent minus what the list derives. */
export const agentRecordOf = (a) => {
  const { usedIn, formerlyIn, latest, id, updatedAt, lastSyncedAt, origin, firstEnvName, versions, ...record } = a || {};
  return record;
};

/** Scope choices with their counts: all, each group, must-pass, the current selection. */
export const copyScopesOf = (env, envState, selectedIds = []) => {
  const live = (envState?.scenarios || []).filter((s) => !s.archived && !s.retired);
  const liveIds = new Set(live.map((s) => s.id));
  const must = (() => {
    try {
      return envMustPassOf(env, envState).gated.map((t) => t.scenarioId || t.id).filter((id) => liveIds.has(id));
    } catch {
      return [];
    }
  })();
  return [
    { id: "all", label: "All scenarios", ids: live.map((s) => s.id) },
    ...groupsOf(envState).map((g) => ({ id: `group:${g.id}`, label: `Group · ${g.name}`, ids: g.ids.filter((id) => liveIds.has(id)) })),
    ...(must.length ? [{ id: "must", label: "Must-pass only", ids: must }] : []),
    ...(selectedIds.length ? [{ id: "selected", label: "Selected scenarios", ids: selectedIds.filter((id) => liveIds.has(id)) }] : []),
  ].filter((s) => s.ids.length);
};

/** What the copy is and how it compares, said before it's made. */
export const copyPlanOf = ({ sameAgent, scope, total, runs }) => {
  const kind = sameAgent ? "duplicate" : "fork";
  const n = scope.ids.length;
  const comparability = !sameAgent
    ? "A fork: another agent, so it compares with the source only on shared cases, and only once its tools are fitted."
    : n === total
      ? `Compares with the source on all ${total} cases while neither changes its graded content.`
      : `Compares with the source only on the ${n} cases it keeps — never as the result for all ${total}.`;
  return {
    kind,
    comparability,
    history: runs
      ? `${runs} run${runs === 1 ? "" : "s"} stay with the source, linked as evidence — new runs belong to the copy.`
      : "The source has no runs yet; new runs belong to the copy.",
  };
};

/** The same copy from the CLI. */
export const envCopyCli = ({ env, name, version, agentName, scopeId }) =>
  `agent-learn env copy ${env.id} --name "${name}" --version ${version}${agentName ? ` --agent "${agentName}"` : ""}${scopeId && scopeId !== "all" ? ` --scope ${scopeId.replace(/^group:/, "group=")}` : ""}`;
