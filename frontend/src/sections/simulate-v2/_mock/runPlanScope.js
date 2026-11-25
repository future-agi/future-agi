/*
  Pure helpers behind the run plan's scope list, readiness check and the
  re-run drawer on the run results screen.
*/
import { paths } from "src/routes/paths";
import { scenarioTitle } from "./scenarios";

/* Scope rows for the plan's checklist: what can run, then what can't (and why).
   A re-run on the source run's own environment version (`rerun`) plays every
   scenario that run played: ones that aren't proved now stay runnable and are
   tagged `notProved` instead of being blocked. */
export const scopeRowsOf = (plan, statusOf = {}, opts = {}) =>
  withActorOnRepeats(scopeRowsRaw(plan, statusOf, opts));

/* Two scenarios with one title are told apart by who's calling. */
const withActorOnRepeats = (rows) => {
  const n = {};
  rows.forEach((r) => (n[r.title] = (n[r.title] || 0) + 1));
  return rows.map(({ actor, ...r }) =>
    n[r.title] > 1 && actor ? { ...r, actor } : r,
  );
};

const scopeRowsRaw = (plan, statusOf, { rerun = false } = {}) =>
  plan
    ? [
        ...plan.run.map((sc) => ({
          id: sc.id,
          title: scenarioTitle(sc.title || sc.name || sc.id),
          actor: sc.persona?.name || null,
          ok: true,
          status: statusOf[sc.id] || null,
        })),
        ...plan.skipped.map((sc) => ({
          id: sc.id,
          title: scenarioTitle(sc.title || sc.name || sc.id),
          actor: sc.persona?.name || null,
          ok: rerun,
          ...(rerun && { notProved: true }),
          why: sc.skipReason,
          status: statusOf[sc.id] || null,
        })),
      ]
    : [];

/* The ids ticked when the plan opens: the ones asked for (the rows chosen on
   the results page, a selection in the table) that can run, else all that can. */
export const initialPickOf = (rows, wanted) => {
  const ok = rows.filter((r) => r.ok).map((r) => r.id);
  const hit = wanted?.length ? ok.filter((id) => wanted.includes(id)) : [];
  return hit.length ? hit : ok;
};

/* Agent × agent version × environment version: what a readiness check covers. */
export const pairIdOf = (agentKey, agentVersion, envVersion) =>
  `${agentKey || ""}|${agentVersion || ""}|${envVersion || ""}`;

/* The check is shown (and must finish) whenever the pair isn't the source
   run's and hasn't been checked in this plan. */
export const needsCheckOf = ({ hasCheck, pairId, baseId, checked }) =>
  !!hasCheck && pairId !== baseId && !checked?.[pairId];

/* What to fix, from the readiness areas. */
export const checkProblemsOf = (areas = []) =>
  areas
    .filter((a) => a.status === "blocked")
    .map((a) => ({ id: a.id, label: a.label, detail: a.detail }));

export const FLAKY_HELP = "Pick 3 or more to spot flaky scenarios";
/* Nothing at 3 or more. */
export const trialsHelpOf = (n) =>
  Number(n) >= 1 && Number(n) < 3 ? FLAKY_HELP : "";

/* The flaky re-run button on the results page: carries the verdict. */
export const flakyRerunLabel = (n, trials) =>
  `Re-run ${n} flaky · ${trials} trials`;

/* The live-run address a started plan goes to (same as the environment page). */
export const launchUrlOf = (
  envId,
  runId,
  scenarioIds,
  trials,
  {
    agent,
    cap,
    alertAt,
    continues,
    snapshot,
    envVersion,
    candidate,
    limits,
    toolReplay,
    compatSubset,
    withAgent,
    rerunOf,
  } = {},
) => {
  const url = paths.dashboard.simulate.simulationRun(envId, runId);
  const params = new URLSearchParams();
  if (agent) params.set("agent", agent);
  if (Array.isArray(scenarioIds) && scenarioIds.length > 0)
    params.set("only", scenarioIds.join(","));
  const k = Number(trials);
  if (Number.isFinite(k) && k >= 1)
    params.set("trials", String(Math.min(20, Math.floor(k))));
  if (cap) params.set("cap", String(cap));
  if (alertAt) params.set("warn", String(Math.round(alertAt * 100)));
  if (continues) params.set("continues", continues);
  if (snapshot) params.set("snapshot", snapshot);
  if (envVersion) params.set("env", envVersion);
  if (candidate) params.set("candidate", candidate);
  if (limits?.trialTimeout) params.set("timeout", String(limits.trialTimeout));
  if (limits?.runDeadline) params.set("deadline", String(limits.runDeadline));
  if (limits?.stepLimit) params.set("steps", String(limits.stepLimit));
  if (toolReplay) params.set("tools", toolReplay);
  if (withAgent) params.set("with", withAgent.key);
  if (rerunOf) params.set("rerun", rerunOf);
  if (compatSubset) {
    params.set("requested", String(compatSubset.requested));
    params.set("blocked", compatSubset.blocked.join(","));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
};
