/*
  Test conditions on one scenario (world.md, Set the test conditions): a
  tool the scenario uses misbehaves on purpose — payments time out, the
  item isn't found, approval is required. The run applies it to that
  scenario only and records it as an injected failure, never as an outage.

  A timeout on a write names where it applies — the write didn't commit, or
  it committed and only the response was lost. They are different tests.

  Saved on the scenario: row.testCondition = { tool, kind, commit? }, with
  commit "before" | "after" only for a timeout on a write tool.
*/
import { replicationOf } from "./replication";
import { sideEffectOf } from "./toolEnv";

export const CONDITIONS = [
  { kind: "timeout", label: "Times out" },
  { kind: "error", label: "Returns an error" },
  { kind: "missing", label: "Finds no such record" },
  { kind: "permission", label: "Needs approval first" },
  { kind: "approval_refused", label: "Approval refused" },
];

/* Where a timeout on a write applies: before it commits, or after. */
export const COMMIT_POINTS = [
  { id: "before", label: "Didn't commit" },
  { id: "after", label: "Committed, response lost" },
];

/** Whether a timeout on this tool needs a commit point — only a write can have committed. */
export const takesCommitPoint = (tool, kind) =>
  kind === "timeout" && !!tool && sideEffectOf({ name: tool }) === "write";

export const conditionLabelOf = (c) => {
  if (!c) return null;
  const base = `${c.tool} · ${(CONDITIONS.find((x) => x.kind === c.kind)?.label || c.kind).toLowerCase()}`;
  if (!takesCommitPoint(c.tool, c.kind)) return base;
  const point = COMMIT_POINTS.find((x) => x.id === (c.commit || "before"));
  return `${base} — ${point.label.toLowerCase()}`;
};

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
