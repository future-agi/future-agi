import { paths } from "src/routes/paths";
import { improvementRunName } from "./optimizer";
import { runEnvVersion } from "./toolFit";
import { snapshotOfRun } from "./envSnapshot";
import { envVersionNameOf } from "./codeRefs";

/**
 * What a run is called, everywhere it is named.
 *
 * A run is a pairing — agent version × environment version — so every screen
 * that names one says both halves the same way: "agent v3 × env v2". The runs
 * list, the run header, the live view and Compare used to each assemble their
 * own ("agent v3", "agent v3 on environment v2", "× env v2" in a detail line),
 * which made one run read as three different things depending on where it was
 * reached from.
 *
 * A run an improvement produced also carries that improvement — a trial of its
 * search, or a "Run winner on full suite" — so the run can say where it came
 * from and link back to it.
 */

/* A trial's id is `${OPT-XXXXX}-t${n}`. */
const TRIAL_ID = /^(OPT-\d+)-t(\d+)$/;

/** The improvement run that produced this run, and how — or null. */
export const improvementOf = (run, envState) => {
  const records = envState?.optimizations || [];
  const id = run?.id || "";
  const trialOf = run?.selfImprovementId || TRIAL_ID.exec(id)?.[1];
  if (trialOf) {
    const record = records.find((o) => o.id === trialOf);
    return record ? { record, role: "trial" } : null;
  }
  const record = records.find((o) => (o.verifyRunIds || []).includes(id));
  if (record) return { record, role: "winner" };
  /* A fix's first step measures today as a run of the environment — the
     version in production, unchanged — and names the fix it was for. */
  const measuredFor = run?.measuredFor ? records.find((o) => o.fromRunId === id) : null;
  return measuredFor ? { record: measuredFor, role: "baseline" } : null;
};

/**
 * `{ agent, env, label, agentLabel, improvement }` for one run.
 *
 *   agent        "v3", or "v3 + change" / "v3 + winner" when an
 *                improvement ran its change on top of v3.
 *   env          "v7" — the environment snapshot the run was taken on
 *                (envSnapshot.js): one number for the world, scenario set,
 *                sub-goals, evals and caller it pinned. Null when there is
 *                neither a stamp nor an environment to read it against, and
 *                the label drops it rather than reading "env unknown".
 *   envStored    the world version as stored ("v3") — for matching, never
 *                for display.
 *   snapshot     the snapshot itself ({ label, parts, summary }), or null.
 *   label        "agent v3 × env v7". A run of an agent the environment has
 *                since swapped out keeps that agent's name in place of "agent".
 *   agentLabel   "agent v3" — the first half, for a screen that sets the env
 *                half in a quieter tone.
 *   improvement  { id, name, href, role } or null. `href` opens the improvement
 *                on the run it started from; null if it has none.
 */
export const runIdentityOf = (run, envState, env) => {
  const from = improvementOf(run, envState);
  /* Trials without a source run stamp "trial" as their version — not one. */
  const base = run?.agentVersion && run.agentVersion !== "trial" && run.agentVersion !== "—"
    ? run.agentVersion
    : null;
  const suffix = { trial: " + change", winner: " + winner" }[from?.role] || "";
  const agent = base ? `${base}${suffix}` : (from?.role === "trial" ? "change" : null);
  const envStored = run?.envVersion || (env && run ? runEnvVersion(env, envState, run) : null);
  /* Shown as the environment snapshot — every part the run held fixed, one
     number — not the world version underneath it. */
  const snapshot = (envStored || run?.envSnapshot) && run ? snapshotOfRun(run, env, envState) : null;
  const envLabel = snapshot?.label || run?.envSnapshot || null;
  const who = run?.agentName || "agent";
  /* A code agent's version reads as its commit — "agent main @ 3f9a2c1".
     Only for the agent the environment tests now: a run of one it has
     since swapped out keeps that agent's own label. */
  const shown = base && !run?.agentName ? `${envVersionNameOf(envState, base, env?.id || run?.envId).main}${suffix}` : agent;
  /* No version stamped (a queued row before an agent is connected) reads as
     a dash, the way an empty cell does — never as a bare "agent". */
  const agentLabel = `${who} ${shown || "—"}`;
  const label = [agentLabel, envLabel && `environment ${envLabel}`].filter(Boolean).join(" × ");

  const record = from?.record;
  const envId = env?.id || run?.envId;
  const improvement = record ? {
    id: record.id,
    name: improvementRunName(envState, record),
    /* The improvement it belongs to — its page lists every run it tried. */
    href: envId ? `${paths.dashboard.simulate.improvements}/${envId}/${record.id}` : null,
    role: from.role,
  } : null;

  return { agent, env: envLabel || null, envStored: envStored || null, snapshot, label, agentLabel, improvement };
};

/**
 * A candidate run: one an improvement made to try a change — a trial of its
 * search, or the winner run on the full suite — rather than one someone
 * started. Kept out of the run history by default and never offered for
 * Debug failures: the improvement already reads its own failures, and paying
 * to analyse a change nobody has accepted yet is waste.
 */
export const isCandidateRun = (run, envState) => run?.kind === "trial"
  || TRIAL_ID.test(run?.id || "")
  || !!improvementOf(run, envState);
