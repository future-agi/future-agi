/*
  Verdict causes for one run's results (PRD v7 §4.10.10 · AC-12.19–12.25).

  Every screen that shows a run's results — the Scenarios tab, the scenario
  drawer, Insights, the shared report, the digest — reads each scenario's
  verdict and cause from here, so they cannot disagree.

    · exploration — a proposed scenario that hasn't been added to the
      environment yet. Carries its cause like any other, but its results
      show separately, outside the main rates.
    · pulled — test invalid twice on a run with the updated RSI PRD's
      outcomes: taken out of the run and flagged for review. Outside every
      count, listed on its own.
    · stale — the scenario was Outdated, Re-check or Stale (an agent change
      removed a tool it needs) when the run started (`run.staleScenarios`,
      pinned on new runs), or it needs a tool the run's agent version lacks.
      It ran and carries its cause, but counts toward no decision until
      updated or archived.
    · otherwise `causeOf(task)`.

  Every verdict carries its outcome — passed · failed · inconclusive ·
  test-invalid · error — with the v7 cause as the detail under it.
*/
import { OUTCOMES, VERDICT_CAUSES, staleForAgentOf, causeOf, outcomeOfCause } from "./decoupled";
import { scenarioStateFor } from "./scenarioStates";
import { knownToolsFor, requiredToolsOf } from "./toolFit";

/** The order causes are listed in, everywhere — grouped by outcome. */
export const CAUSE_ORDER = [
  "pass",
  "fail-agent", "fail-required",
  "inc-evidence",
  "inc-simulator", "inc-setup", "inc-judge",
  "inc-infra", "inc-transport",
];

/** Short labels for tight places — "agent", "simulator". */
export const causeShort = (cause) => (VERDICT_CAUSES[cause]?.label || cause).replace(/^(passed|failed|inconclusive|test invalid|error) · /i, "");

/** "Test invalid", "Error" — the outcome a cause rolls up to, as a label. */
export const outcomeLabelOf = (cause) => OUTCOMES[outcomeOfCause(cause)]?.label || "Unmeasured";

/**
 * The scenarios that were Outdated, Re-check or Stale as a run starts — they
 * still run, but count toward no decision until updated or archived. Pinned
 * on the new run as `staleScenarios` ({ id: "outdated" | "recheck" | "stale" }),
 * so a later rewrite doesn't change what an old run counted. Null when none
 * are. `agentLabel` is the agent version the run tests.
 */
export const staleScenariosOf = (env, envState, rows = [], agentLabel = null) => {
  const known = knownToolsFor(env, envState);
  const out = {};
  rows.forEach((r) => {
    const st = scenarioStateFor(r, { id: "proved" }, { env, envState, tools: requiredToolsOf(r, known), agentLabel });
    if (st.id === "outdated" || st.id === "recheck" || st.id === "stale") out[r.id] = st.id;
  });
  return Object.keys(out).length ? out : null;
};

export const STALE_LABEL = { outdated: "Outdated", recheck: "Re-check", stale: "Stale" };

/**
 * Each task's verdict, by id:
 * { pulled, reason, label } |
 * { cause, outcome, label, scored, required?, retried?, stale? }
 */
export const taskVerdictsOf = ({ env, envState, run, tasks = [] }) => {
  const agent = run?.agentVersion || null;
  const rows = new Map((envState?.scenarios || []).map((r) => [r.id, r]));
  const stale = run?.staleScenarios || {};
  const out = new Map();
  tasks.forEach((t) => {
    /* Test invalid twice — taken out of the run, flagged for review. */
    if (t.pulled) {
      out.set(t.id, {
        pulled: true, label: "Pulled for review", outcome: "test-invalid", cause: causeOf(t), reason: t.pulled.reason, retried: t.retried || null,
      });
      return;
    }
    const row = rows.get(t.id) || rows.get(t.scenarioId) || t;
    const failed = t.status !== "passed" && t.status !== "unmeasured" && t.measured !== false;
    const cause = causeOf(t);
    const candidate = !!row.proposed;
    /* Needs a tool this agent version lacks — stale, even on a run that
       didn't pin it. */
    const staleFor = !stale[t.id] && agent && staleForAgentOf(row, agent, env, envState).stale ? "stale" : null;
    out.set(t.id, {
      cause,
      outcome: outcomeOfCause(cause),
      ...(candidate && { exploration: true, candidate }),
      label: VERDICT_CAUSES[cause]?.label || OUTCOMES[outcomeOfCause(cause)]?.label || "Failed",
      scored: VERDICT_CAUSES[cause]?.scored ?? true,
      ...(cause === "fail-required" && t.requiredFail && { required: t.requiredFail }),
      ...(t.retried && { retried: t.retried }),
      ...((stale[t.id] || staleFor) && { stale: stale[t.id] || staleFor }),
      /* Stopped at the turn limit (runs that pinned `turnLimits`). */
      ...(t.turnLimit && failed && { turnLimit: t.turnLimit }),
    });
  });
  return out;
};

/** What a scenario's verdict reads as in one line — "Failed · agent". */
export const verdictLineOf = (v) => {
  if (!v) return null;
  if (v.pulled) return v.reason;
  if (v.cause === "fail-required") return `Failed a required evaluation · ${v.required?.name || "required evaluation"}`;
  if (v.turnLimit) return `${v.label} — Hit the turn limit (${v.turnLimit.limit})`;
  return v.label;
};
