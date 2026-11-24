/*
  Test conditions on one scenario (world.md, Set the test conditions): a
  tool the scenario uses misbehaves on purpose. The run applies it to that
  scenario only and records it as an injected failure, never as an outage.

  The five kinds the backend runs (Karthik's review, 23 Nov, #22). A lost
  answer is its own kind, not a sub-choice of a timeout: the call ran — a
  write landed — and only the answer never came back.

  Saved on the scenario: row.testCondition = { tool, kind, call, approval },
  with call a call number (1, 2, 3…) — a condition fails the tool on one
  chosen call, never on every call (Karthik, 24 Nov) — and approval the
  approval action that records it, which "Needs approval first" must name
  or the backend refuses the save.

  Older saves: "first" reads as call 1. "every" has no backend meaning; it
  reads as call 1 too — the call it always hit first — and says it was
  saved as every call until it is saved again.
*/
import { replicationOf } from "./replication";
import { worldActionsOf } from "./actors";

export const CONDITIONS = [
  { kind: "timeout", label: "Times out before it runs" },
  { kind: "after_commit", label: "Runs, then the answer is lost" },
  { kind: "unavailable", label: "Unavailable" },
  { kind: "error", label: "Answers with an error" },
  { kind: "permission", label: "Needs approval first" },
];

/*
  Kinds saved before the five above. Read, never offered: a lost answer was
  a timeout with commit "after"; a refused approval reads as needing one;
  "no such record" has no backend kind, so it shows as it was.
*/
const RETIRED = {
  missing: "Finds no such record",
  approval_refused: "Approval refused",
};

/** Which call it hits: one call number. Offered up to MAX_CALL. */
export const MAX_CALL = 10;
export const CALLS = Array.from({ length: MAX_CALL }, (_, i) => ({
  id: i + 1,
  label: `Call ${i + 1}`,
}));

/** The kind that needs an approval action named. */
export const needsApprovalAction = (kind) => kind === "permission";

/**
 * The environment's approval actions a "Needs approval first" condition can
 * name: approvals.change and approve_<tool> for each tool that writes.
 */
export const approvalActionsForConditions = (env, envState) =>
  worldActionsOf(env, envState)
    .filter((a) => a.kind === "approval")
    .map((a) => a.name);

export const APPROVAL_MISSING =
  "Pick the approval action that records it — the save is refused without one";

/** A saved condition as the five kinds read it; old shapes mapped across. */
export const normalizeCondition = (c) => {
  if (!c) return null;
  const kind =
    c.kind === "timeout" && c.commit === "after"
      ? "after_commit"
      : c.kind === "approval_refused"
        ? "permission"
        : c.kind;
  const n = Number(c.call);
  const call = Number.isInteger(n) && n >= 1 ? n : 1;
  return {
    tool: c.tool,
    kind,
    call,
    approval: c.approval || null,
    /* Saved as "every call" before calls were one number. */
    ...(c.call === "every" && { wasEvery: true }),
  };
};

/** A kind that's no longer offered — kept as saved, shown under its old name. */
export const isRetiredKind = (kind) => !!RETIRED[kind];

export const callLabelOf = (call) =>
  `call ${Number.isInteger(Number(call)) && Number(call) >= 1 ? Number(call) : 1}`;

/** A kind's name, offered or retired. */
export const kindLabelOf = (kind) =>
  CONDITIONS.find((x) => x.kind === kind)?.label || RETIRED[kind] || kind;

export const conditionLabelOf = (raw) => {
  const c = normalizeCondition(raw);
  if (!c) return null;
  return [
    c.tool,
    kindLabelOf(c.kind).toLowerCase(),
    needsApprovalAction(c.kind) && c.approval,
    `${callLabelOf(c.call)}${c.wasEvery ? " (saved as every call)" : ""}`,
  ]
    .filter(Boolean)
    .join(" · ");
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
          "This environment's tools are live, so a failure can't be forced. Use a simulated copy of the environment to test it.",
      }
    : { ok: true, reason: null };
};
