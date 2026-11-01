/*
  Test conditions on one scenario (world.md, Set the test conditions): a
  tool the scenario uses misbehaves on purpose — payments time out, the
  item isn't found, approval is required. The run applies it to that
  scenario only and records it as an injected failure, never as an outage.

  Saved on the scenario: row.testCondition = { tool, kind }.
*/
import { replicationOf } from "./replication";

export const CONDITIONS = [
  { kind: "timeout", label: "Times out" },
  { kind: "error", label: "Returns an error" },
  { kind: "missing", label: "Finds no such record" },
  { kind: "permission", label: "Needs approval first" },
];

export const conditionLabelOf = (c) =>
  c
    ? `${c.tool} · ${(CONDITIONS.find((x) => x.kind === c.kind)?.label || c.kind).toLowerCase()}`
    : null;

/**
 * Whether this environment can fail a tool on purpose. Live tools reach
 * the agent's real systems, so nothing can be injected deterministically.
 */
export const conditionSupportOf = (env, envState) => {
  let live = false;
  try {
    /* No agent yet: only the world's simulated tools, so it can. */
    live =
      !!envState?.agent &&
      replicationOf(envState.agent, env)?.proofKind === "live";
  } catch {
    live = false;
  }
  return live
    ? {
        ok: false,
        reason:
          "This environment's tools are live, so a failure can't be forced. Use a sealed copy of the environment to test it.",
      }
    : { ok: true, reason: null };
};
