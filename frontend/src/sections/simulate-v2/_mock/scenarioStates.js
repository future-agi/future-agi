/**
 * The Scenarios tab's reading of the versioning doc's scenario states
 * valid · re-check · outdated · stale · archived.
 *
 * `scenarioStateOf` (envModel.js) answers the doc's two questions from what a
 * row already carries — its proof, a confirmed spec change. This refines it:
 *   · archived — a hand edit, reversible (`row.archived = { at, note }`)
 *   · stale — an agent change removed a tool the scenario needs (RSI PRD;
 *     decoupled.js `staleForAgentOf`). It still runs, but counts toward no
 *     decision until it is updated or archived.
 *   · a rule added (or re-worded) on a flow the scenario walks through: the
 *     scenario still stages and passes its reference solution, but that
 *     solution was written before the rule existed — Re-check, not Valid,
 *     until someone re-proves it.
 * Nothing is retired in v7. A stored `row.retired` reads as archived.
 *
 * Also here: archive / restore, which actions each state offers, and the
 * proof gates in the wording of the world the scenario runs against.
 */
import { scenarioStateOf, SCENARIO_STATES, rulesOf, ruleOfScenario, isConnectOnly } from "./envModel";
import { subTasksFor, validate } from "./contract";
import { reproved } from "./proofs";
import { environmentVersions, envLineage, holdsInEnv } from "./versions";
import { admissionGatesOf, declineKindOf, evidenceGateOf, failedAdmissionGate } from "./admissionGates";
import { staleForAgentOf } from "./decoupled";

/* Outdated is the one state that earns colour — the existing red, used
   sparingly. Everything else stays neutral. */
export const STATE_TONE = { outdated: "#DC2626" };

/* The states this tab adds to the doc's. */
export const EXTRA_STATES = {
  archived: { label: "Archived", note: "kept in the set, not run or counted" },
  stale: { label: "Stale", note: "an agent change removed a tool it needs" },
  /* Viewing an older environment version: added after it. Not a state the
     filter offers — "new" is a compare band, not a scenario state. */
  later: { label: "Later version", note: "added after this environment version" },
};

/* The filter's state choices, in the doc's order. */
export const STATE_CHOICES = [
  SCENARIO_STATES.valid.label, SCENARIO_STATES.recheck.label, SCENARIO_STATES.outdated.label,
  EXTRA_STATES.stale.label, EXTRA_STATES.archived.label, SCENARIO_STATES.blocked.label,
];

/** Archived by hand — or retired before v7, which reads the same. */
export const archivedOf = (row) => row?.archived || (row?.retired ? { at: row.retired.at, note: row.retired.why || null } : null);

/** A stored pre-v7 row: `retired` becomes `archived`, nothing else moves. */
export const migrateRetired = (row) => {
  if (!row?.retired) return row;
  const { retired, ...rest } = row;
  return rest.archived ? rest : { ...rest, archived: { at: retired.at || null, note: retired.why || null } };
};

/* ── rule changes on a scenario's flow ───────────────────────────────────── */

const changeKey = (c) => `${c.id}@${c.envVersion || ""}`;

/* Every tool the scenario touches: the ones it needs, the reference flow's
   calls, and any sub-goal that reads "Call <tool> …". */
const toolsOnFlow = (row, env, tools = []) => {
  const out = new Set(tools);
  (validate(row, env)?.reference || []).forEach((r) => r.tool && r.tool !== "respond" && out.add(r.tool));
  const goals = Array.isArray(row?.subGoals) && row.subGoals.length ? row.subGoals : subTasksFor(row, env);
  goals.forEach((g) => {
    const text = typeof g === "string" ? g : g?.text || g?.label || "";
    const call = text.match(/^Call ([a-z0-9_]+)/i)?.[1];
    if (call) out.add(call);
  });
  return out;
};

/* A rule names a tool when it carries it, or when every word of the tool's
   name appears in its text ("issue_refund" ↔ "never issue a refund …"). */
const ruleNamesTool = (change, tool) => {
  if ((change.tools || []).includes(tool) || change.tool === tool) return true;
  const text = String(change.to || change.text || "").toLowerCase();
  const words = tool.toLowerCase().split(/[_\s-]+/).filter((w) => w.length > 2);
  return words.length > 0 && words.every((w) => text.includes(w));
};

/**
 * The added or re-worded rules that sit on this scenario's flow and that it
 * hasn't been re-proved against since. A confirmed spec change on the rule it
 * tests is `outdatedBy` — Outdated, handled before this is asked.
 */
export const ruleChangesOnFlow = (row, { env, envState, tools } = {}) => {
  const lineage = envLineage(env, envState);
  const changes = (envState?.ruleChanges || []).filter((c) => (c.kind === "added" || c.kind === "changed") && holdsInEnv(lineage, c.envVersion));
  if (!changes.length || !row) return [];
  const checked = new Set(row.ruleChecked || []);
  const flow = toolsOnFlow(row, env, tools);
  const tested = ruleOfScenario(row, rulesOf(env, envState))?.id;
  return changes.filter((c) => {
    if (checked.has(changeKey(c))) return false;
    if ((c.scenarios || []).includes(row.id)) return true;
    if (c.kind === "changed" && c.id === tested) return true;
    return [...flow].some((t) => ruleNamesTool(c, t));
  });
};

/**
 * The state the tab shows: archived, then stale for the agent version
 * (`ctx.agentLabel`), then the doc's state refined by rule changes on
 * the scenario's flow. `ctx` = { env, envState, tools (the ones it needs),
 * agentLabel }.
 */
export const scenarioStateFor = (row, status, ctx = {}) => {
  const archived = archivedOf(row);
  if (archived) {
    return {
      id: "archived", ...EXTRA_STATES.archived, canRun: null, correct: null, proof: status,
      why: archived.note ? `Archived by hand — ${archived.note.replace(/\.$/, "")}.` : "Archived by hand. Restore it to run and count it again.",
    };
  }
  if (ctx.agentLabel && ctx.env && ctx.envState) {
    const st = staleForAgentOf(row, ctx.agentLabel, ctx.env, ctx.envState);
    if (st.stale) {
      return {
        id: "stale", ...EXTRA_STATES.stale, canRun: true, correct: null, proof: status,
        capability: st.capability,
        why: `${st.reason}. Update it so it no longer needs ${st.capability}, or archive it.`,
      };
    }
  }
  const raw = scenarioStateOf(row, status, ctx.env && ctx.envState ? environmentVersions(ctx.env, ctx.envState) : []);
  const base = raw.id === "new" ? { ...raw, id: "later", ...EXTRA_STATES.later } : raw;
  /* A sub-goal added, removed or re-marked changes what a pass means — the
     proof is of the old grading. Outdated already says more. */
  if (row?.subGoalsUnproved && (base.id === "valid" || base.id === "recheck")) {
    return {
      ...base,
      id: "recheck",
      ...SCENARIO_STATES.recheck,
      canRun: base.canRun ?? true,
      correct: null,
      proof: status,
      gradeNote: "Can't be graded until re-proved",
      why: "Its sub-goals changed — can't be graded until re-proved.",
    };
  }
  if (base.id !== "valid") return { ...base, proof: status };
  const flow = ruleChangesOnFlow(row, ctx);
  if (!flow.length) return { ...base, proof: status };
  const c = flow[flow.length - 1];
  return {
    id: "recheck",
    ...SCENARIO_STATES.recheck,
    canRun: true,
    correct: null,
    proof: status,
    ruleChange: c,
    why: c.kind === "added"
      ? "A rule was added on this flow — its reference solution may no longer be the intended behaviour."
      : `A rule on this flow changed in ${c.envVersion || "a later version"} — its reference solution may no longer be the intended behaviour.`,
  };
};

/** Waiting on someone: Outdated, Stale, or Re-check that isn't mid-build. */
export const needsReview = (state) => (state.id === "outdated" || state.id === "stale" || state.id === "recheck") && state.proof?.id !== "checking";

/*
  Stale scenarios count toward no decision (RSI PRD). An Outdated, Re-check or
  Stale scenario still runs, but its result is left out of the run's summary and
  of what an improvement decides on, until it is updated or archived.
*/
export const NO_DECISION_NOTE = "Still runs, but counts toward no decision until it's updated or archived.";
export const countsTowardNoDecision = (state) => !!state && (state.id === "outdated" || state.id === "recheck" || state.id === "stale");

/**
 * The one thing each state asks for. A quarantined scenario can't be proved
 * as written, so it is rewritten rather than re-proved.
 */
export const stateActionOf = (state) => {
  if (state.id === "archived") return "restore";
  if (state.id === "outdated") return "rewrite";
  /* Stale: update it so it no longer needs the removed tool — the editor. */
  if (state.id === "stale") return "edit";
  if (state.id === "recheck") {
    if (state.proof?.id === "checking") return null;
    return state.proof?.id === "quarantined" ? "rewrite" : "reprove";
  }
  return null;
};

export const STATE_ACTION_LABEL = { reprove: "Re-prove", rewrite: "Rewrite", edit: "Update", restore: "Restore" };

/* ── archive, restore, re-prove, rewrite ─────────────────────────────── */

/**
 * Archived by hand: it stays in the set, listed and filterable, but doesn't
 * run or count. Reversible with Restore. `note` is optional.
 */
export const archiveScenario = (row, note) => {
  const { retired, ...rest } = row;
  return { ...rest, archived: { at: new Date().toISOString(), note: note?.trim() || null } };
};

export const restoreScenario = (row) => {
  const { retired, archived, ...rest } = row;
  /* Counted so the suite version keeps climbing — restoring is a change too. */
  return { ...rest, restores: (row.restores || 0) + 1 };
};

/** Live rows — the ones a run takes and the counts mean. */
export const liveScenarios = (rows = []) => rows.filter((r) => !archivedOf(r));

/**
 * Re-prove against the pinned world, and record the rule changes on the
 * flow it has now been proved against, so they stop asking.
 */
export const reproveScenarios = (rows, { env, envState, toolsOf }) =>
  reproved(rows, env, envState).map((r, i) => {
    const flow = ruleChangesOnFlow(rows[i], { env, envState, tools: toolsOf ? toolsOf(rows[i]) : [] });
    const done = { ...r, subGoalsUnproved: false };
    return flow.length ? { ...done, ruleChecked: [...new Set([...(r.ruleChecked || []), ...flow.map(changeKey)])] } : done;
  });

/** A rewrite answers the spec change that made it Outdated; its proof is now of the new text. */
export const rewrittenScenario = (row) => {
  if (!row?.outdatedBy) return row;
  const { outdatedBy, ...rest } = row;
  return { ...rest, rewroteFor: outdatedBy };
};

/* ── the proof's gates ───────────────────────────────────────────────────── */

/* Which gate a world change can break. */
const GATE_OF_REASON = { seed: "ready", contract: "solvable", checks: "vacuous" };

/**
 * The gates a proof passes, worded for the environment — the three below,
 * then the admission gates (admissionGates.js): mutation controls,
 * alternate valid paths, setup can't earn credit, exploit control. The
 * first three carry `core`; the proof row shows them inline and the rest
 * behind "+N more".
 *
 * A world is staged and solved by a reference solution of tool calls; a
 * connect-only environment has no world, so its proof is a reference
 * transcript graded by the judge, and "ready" means the fixture's caller
 * account exists.
 *
 * One swap: a refusal, guardrail or transfer scenario — where declining is
 * correct — can't use "an empty run fails it". It needs positive evidence
 * instead: a required utterance or a transfer call.
 *
 * Each gate is `ok` (true), failed (false) or unknown (null, not yet proved
 * on this version). `broken` is the row breaking on the pinned world.
 */
export const proofGatesOf = (row, env, { stale, broken } = {}) => {
  const connect = isConnectOnly(env);
  const broke = new Set((row?.brokeReasons || []).map((r) => GATE_OF_REASON[r]).filter(Boolean));
  const result = (id) => {
    /* Broke without a recorded reason: it no longer stages; the rest is unknown. */
    if (broken) return broke.size ? !broke.has(id) : (id === "ready" ? false : null);
    return stale ? null : true;
  };
  const decline = declineKindOf(row);
  const failed = failedAdmissionGate(row, env);
  const core = (connect
    ? [
      { id: "ready", label: "Ready", detail: "The fixture's caller account is in place." },
      { id: "solvable", label: "Solvable", detail: "The reference transcript passes the judge." },
      { id: "vacuous", label: "Not vacuous", detail: "An empty call fails it." },
    ]
    : [
      { id: "ready", label: "Staged", detail: "The world holds what it presumes." },
      { id: "solvable", label: "Solvable", detail: "A reference solution passes every check." },
      { id: "vacuous", label: "Not vacuous", detail: "An empty run fails it." },
    ]).map((g) => {
    if (g.id === "vacuous" && decline) {
      const ev = evidenceGateOf(decline, connect);
      return { ...g, id: "evidence", label: ev.label, detail: ev.detail, file: ev.file, failedReason: failed?.gate === "evidence" ? failed.reason : null };
    }
    return g;
  });
  return core.map(({ failedReason, ...g }) => {
    const ok = result(g.id === "evidence" ? "vacuous" : g.id);
    if (failedReason && ok !== null) return { ...g, core: true, ok: false, reason: failedReason };
    return { ...g, core: true, ok };
  }).concat(admissionGatesOf(row, env, { stale, broken }));
};

/* What "can it still run" checks, in the environment's terms. */
export const canRunMeaning = (env) => (isConnectOnly(env)
  ? "every tool it names is still in the contract, and its caller account is in the fixture"
  : "every tool, table and record it names still exists");
