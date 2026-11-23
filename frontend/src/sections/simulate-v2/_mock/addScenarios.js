/**
 * Stamping a new batch of scenarios — the same way whichever route adds it.
 *
 * The Add scenarios drawer, the builder chat on the build screen and the
 * workspace chat all add batches. Each batch records who added it and how,
 * the tools each scenario needs, and the environment version it was added
 * under, so a scenario added on env v2 is not part of env v1 when v1 is
 * pinned again. The scenarios that need a tool this world can't answer are
 * counted, so every route can warn about them.
 *
 * Prove on add. A scenario is stamped proved against this environment
 * version only once it passes the three gates — the starting state it
 * presumes can be staged, a reference solution passes, and its sub-goals
 * fail when the agent does nothing (a declining scenario needs positive
 * evidence instead, so it isn't held to the empty run). One that fails a
 * gate isn't added; the batch says which and why (`rejected`,
 * `rejectedNote`). A row that arrives already proved (rebuilt, or proved
 * where it was written) keeps that proof.
 *
 * Deterministic: the verdict is a fingerprint of the scenario and the
 * environment version, so the same batch on the same world always lands the
 * same way.
 */
import { stampProvenance, defaultBatchId } from "./scenarioProvenance";
import { withSourceKeywords } from "./scenarioKeywords";
import { currentEnvVersion } from "./versions";
import { knownToolsFor, requiredToolsOf, worldToolsFor } from "./toolFit";
import { connectOnlyEnv, declineKindOf } from "./admissionGates";

const fraction = (seed) => {
  const s = String(seed || "");
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 10000) / 10000;
};

export const ADD_GATES = [
  { id: "staged", label: "Starting state ready", fail: "the world can't stage the state it presumes" },
  { id: "solvable", label: "Reference solution passes", fail: "a known-good run doesn't reach the expected end state" },
  { id: "not-vacuous", label: "Sub-goals fail when the agent does nothing", fail: "it passes with no agent at all" },
];

/**
 * The three gates for one new scenario on one environment version.
 * → { pass, gates: [{ id, label, pass, na? }], failed?: { id, label, reason } }
 */
export const proveOnAdd = (row, env, envLabel) => {
  const key = `${row?.id || row?.name || row?.title}::${envLabel}`;
  const connect = connectOnlyEnv(env);
  const declines = !!declineKindOf(row);
  const hasChecks = !!(row?.expected || row?.outcome || (Array.isArray(row?.subGoals) && row.subGoals.length));
  const verdict = {
    /* Nothing to stage in a connect-only environment — it calls its own systems. */
    staged: connect ? null : fraction(`${key}::staged`) >= 0.03,
    solvable: fraction(`${key}::solvable`) >= 0.03,
    /* A refusal, guardrail or transfer passes an empty run by design — it's
       held to positive evidence when it runs, not to this gate. */
    "not-vacuous": declines ? true : hasChecks && fraction(`${key}::vacuous`) >= 0.02,
  };
  const gates = ADD_GATES.map((g) => ({
    id: g.id, label: g.label, pass: verdict[g.id] !== false, ...(verdict[g.id] === null && { na: true }),
  }));
  const first = ADD_GATES.find((g) => verdict[g.id] === false);
  return first
    ? { pass: false, gates, failed: { id: first.id, label: first.label, reason: first.fail } }
    : { pass: true, gates };
};

export const stampNewBatch = (rows, { env, envState, source, actor, at = new Date().toISOString() }) => {
  const envLabel = currentEnvVersion(env, envState).label;
  const known = knownToolsFor(env, envState);
  const answers = new Set(worldToolsFor(env, envState).map((t) => t.name));
  const batchId = defaultBatchId(source, at);
  const rejected = [];
  const stamped = [];
  (rows || []).forEach((r) => {
    const proof = r.provedAgainst ? null : proveOnAdd(r, env, envLabel);
    if (proof && !proof.pass) {
      rejected.push({ id: r.id, /* What the table shows, not the slug. */ name: r.summary || r.title || r.name || r.id, gate: proof.failed.id, reason: proof.failed.reason });
      return;
    }
    stamped.push({
      /* Added from outside: it keeps where it came from as keywords. */
      ...withSourceKeywords(stampProvenance(r, { source, actor, at, batchId })),
      requiredTools: requiredToolsOf(r, known),
      provedAgainst: r.provedAgainst || envLabel,
      provedAt: r.provedAt || at,
      ...(proof && { proofGates: { at, envVersion: envLabel, gates: proof.gates } }),
      addedInEnv: envLabel,
    });
  });
  const unanswerable = [...new Set(stamped.flatMap((r) => r.requiredTools.filter((t) => !answers.has(t))))];
  const needing = stamped.filter((r) => r.requiredTools.some((t) => !answers.has(t))).length;
  return { rows: stamped, envLabel, unanswerable, needing, rejected };
};

/** The warning every add route shows when some of a batch can't be measured yet. */
export const unanswerableNote = ({ needing, unanswerable, envLabel }) => (needing
  ? `${needing} of these need ${unanswerable.join(", ")}, which environment ${envLabel} can't answer — they come back not measured until the environment is rebuilt.`
  : null);

/** What the gates kept out of a batch, said once — or null when every row passed. */
export const rejectedNote = ({ rejected = [] }) => {
  if (!rejected.length) return null;
  const first = rejected[0];
  return rejected.length === 1
    ? `1 wasn't added — “${first.name}” failed its checks: ${first.reason}.`
    : `${rejected.length} weren't added — they failed their checks (“${first.name}”: ${first.reason}${rejected.length > 1 ? `; and ${rejected.length - 1} more` : ""}).`;
};
