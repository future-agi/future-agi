/*
  What each compared run pinned, pin by pin — the compact "what differed"
  disclosure under the pair-class breakdown (PRD v7 §4.10.7). A pin that
  differs no longer makes runs "not comparable": it changes the class of the
  pairs it touches (pairs.js), and the comparison is never blocked.
*/
import { displayEnvLabel, heldFixed, isConnectOnly, runPinsOf, subGoalCatalogOf, subGoalsOfScenario } from "./envModel";
import { environmentVersions } from "./versions";
import { runResultOf } from "./runResult";

/* ── held fixed ──────────────────────────────────────────────────────────── */

/**
 * Every pin across the compared runs, with the distinct values they held in
 * run order. `differs` lists the pins that moved — each one is accounted for
 * in the pair classes, never a reason to refuse.
 */
export const heldFixedAcross = (runs, env, envState) => {
  if (!runs || runs.length < 2) return null;
  const base = heldFixed(runs[0], runs[runs.length - 1], env, envState);
  const versions = environmentVersions(env, envState);
  const pins = runs.map((r) => runPinsOf(r, env, envState));
  const rows = base.rows.map((row) => {
    const values = [...new Set(pins.map((p) => p[row.id]).filter(Boolean))];
    return {
      ...row,
      /* The world version underneath the environment version — a drill-down,
         so it's named as the part it is (E vN). */
      values: row.id === "env" ? values.map((v) => `E ${displayEnvLabel(v, versions)}`) : values,
      same: values.length <= 1,
    };
  });
  const differs = rows.filter((r) => !r.same);
  /* The sample agent's (Reference) runs are another agent, even at the same version label. */
  const agents = [...new Set(pins.map((p, i) => (p.agent && runs[i]?.reference ? `sample ${p.agent}` : p.agent)).filter(Boolean))];
  return { rows, differs, agents, drift: driftOf(runs, env, envState, !differs.some((r) => r.hard) && agents.length <= 1) };
};

/*
  Everything held fixed, the agent too, and the pass rate still moved: the
  cause is outside what we version. Said up front, so the flips below aren't
  read as the agent's. Same 10-point bar as the Runs tab.
*/
const DRIFT_POINTS = 10;
const driftOf = (runs, env, envState, allSame) => {
  if (!allSame) return null;
  const rates = runs.map((r) => runResultOf(env, envState, r)?.rate);
  if (rates.some((x) => x == null)) return null;
  const points = Math.abs(rates[rates.length - 1] - rates[0]);
  if (points <= DRIFT_POINTS) return null;
  return {
    points,
    text: isConnectOnly(env)
      ? `The pass rate moved ${points} pts. This agent calls its own systems, so the cause is most likely there (the caller account, its data) — read the flips below as drift, not as the agent's.`
      : `The pass rate moved ${points} pts — more than repeats explain. Check the agent's live configuration before reading the flips below as the agent's.`,
  };
};

const ran = (c) => !!c && c.status !== "missing";

/* Per-scenario reading — pair class and flip — lives in pairs.js (PRD v7
   §4.10.7). This file keeps the per-pin detail and the sub-goal grouping. */

/* ── grouping ────────────────────────────────────────────────────────────── */

/**
 * One group per shared sub-goal, critical ones first. A scenario sits under
 * every sub-goal it checks. Each group carries a pass rate per run, over the
 * scenarios in it that every run ran and measured — runs record a verdict
 * per scenario, so a sub-goal passes where its scenario did.
 */
export const subGoalGroups = (rows, runs, env, envState) => {
  const catalog = subGoalCatalogOf(env, envState);
  const critical = new Set();
  (envState?.scenarios || []).forEach((sc) => {
    subGoalsOfScenario(sc, env, envState).forEach((g) => { if (g.mark === "critical") critical.add(g.id); });
  });
  const byId = new Map(rows.map((r) => [r.id, r]));
  const placed = new Set();
  const rateOf = (groupRows, i) => {
    const shared = groupRows.filter((r) => r.cells.every((c) => ran(c) && c.measured));
    const shares = shared.map((r) => r.cells[i].passShare).filter((v) => v != null);
    return { rate: shares.length ? Math.round((shares.reduce((a, b) => a + b, 0) / shares.length) * 100) : null, n: shares.length };
  };
  const groups = catalog
    .map((g) => {
      const members = g.usedBy.map((id) => byId.get(id)).filter(Boolean);
      members.forEach((r) => placed.add(r.id));
      return {
        id: g.id,
        label: g.name,
        mark: critical.has(g.id) ? "critical" : "supporting",
        rows: members,
        rates: (runs || []).map((run, i) => ({ runId: run.id, letter: run.letter, color: run.color, ...rateOf(members, i) })),
      };
    })
    .filter((g) => g.rows.length);
  const ordered = [...groups.filter((g) => g.mark === "critical"), ...groups.filter((g) => g.mark !== "critical")];
  const loose = rows.filter((r) => !placed.has(r.id));
  return loose.length ? [...ordered, { id: "no-sub-goal", label: "No shared sub-goal", rows: loose }] : ordered;
};
