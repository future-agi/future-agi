/**
 * The world version (E vN) underneath the environment version people see.
 *
 * E is the contract and the world only — tools, arguments, rules, use cases;
 * schema, seed, tool behaviour, actors. What people see is the environment
 * snapshot (envSnapshot.js): one number for E together with the scenario
 * set, sub-goal catalogue, evals and simulated caller. This file maps world
 * labels to that number and says what saving pending world edits will cost.
 *
 * Derived from the stored history; nothing is migrated.
 */
import { environmentVersions, pendingEnvChanges, nextEnvVersion } from "./versions";
import { displayEnvLabel, subGoalsOfScenario, rulesOf, ruleOfScenario } from "./envModel";
import { snapshotOfWorld, nextSnapshotLabel } from "./envSnapshot";

/* The change kinds that are the world version. */
export const WORLD_KINDS = ["contract", "rules", "seed", "toolbehaviour", "actors", "tools-added", "tools-removed"];

/**
 * A stored world label as it reads on the surface: the environment snapshot
 * (envSnapshot.js) that is newest on that world — "E v3" reads "v7" while v7
 * is the newest snapshot holding world v3. Unknown labels pass through.
 */
export const envLabelOf = (env, envState, label) => (label
  ? snapshotOfWorld(env, envState, label)?.label || displayEnvLabel(label, environmentVersions(env, envState))
  : label);

/* Whether a scenario uses a tool: its text, the tools it records needing,
   its path, and its sub-goals — stored or derived ("Call issue_refund…"),
   since most scenarios never write the tool name in their own text. */
const mentions = (row, name, env, envState) => JSON.stringify([
  row.task, row.expected, row.situation, row.outcome, row.subGoals, row.requiredTools, row.path, row.steps,
  env ? subGoalsOfScenario(row, env, envState).map((g) => g.text) : null,
]).includes(name);

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

/**
 * What saving the pending edits will cost, stated before they land (AC-23.3):
 * which version moves, and what it does to comparability.
 *
 *   Environment edit → v8 · 16 scenarios rechecked
 *
 * Only the environment moves on a world or spec edit (rules, tools, schema,
 * seed, tool behaviour, actors). A rechecked scenario still pairs with past
 * runs — as Rechecked, badged, if its gates pass; outdated if they fail.
 * Everything else compares exactly. Which scenarios depend on an edit is an
 * estimate: those that name a tool the edit names; a rules edit, those that
 * test a rule; reseeded data, those that call a tool at all; actors, every one.
 */
export const pendingCostOf = (env, envState) => {
  const pending = pendingEnvChanges(envState);
  if (!pending.length) return null;
  const kinds = [...new Set(pending.map((c) => c.kind))];
  const next = nextEnvVersion(env, envState);
  /* Saved, the edits mint the next environment snapshot — that's the number
     people see; the world version underneath (E vN) moves with it. */
  const label = nextSnapshotLabel(env, envState);
  const rows = (envState?.scenarios || []).filter((r) => !r.retired && !r.archived);
  const tools = (env?.tools || []).map((t) => t.name);
  const named = tools.filter((n) => pending.some((c) => String(c.detail || "").includes(n)));
  const rules = rulesOf(env, envState);
  const dependent = kinds.includes("actors") ? rows
    : named.length ? rows.filter((r) => named.some((n) => mentions(r, n, env, envState)))
      : kinds.includes("contract") ? rows
        : kinds.includes("rules") ? rows.filter((r) => ruleOfScenario(r, rules))
          : kinds.some((k) => k === "seed" || k === "toolbehaviour") ? rows.filter((r) => tools.some((n) => mentions(r, n, env, envState)))
            : [];
  const n = dependent.length;
  const spec = kinds.some((k) => k === "contract" || k === "rules");
  return {
    major: false,
    moves: "environment",
    label,
    stored: next.label,
    world: `E ${next.label}`,
    scenarios: n,
    text: `${spec ? "Environment spec edit" : "Environment edit"} → environment ${label} · ${n ? `${plural(n, "scenario")} rechecked` : "no scenario rechecked"}`,
    comparability: n
      ? `${plural(n, "scenario")} pair with past runs as Rechecked if their gates pass, outdated if not; ${rows.length - n} compare exactly. Agent, scenario set and evals don't move.`
      : "Every scenario still compares exactly. Agent, scenario set and evals don't move.",
  };
};
