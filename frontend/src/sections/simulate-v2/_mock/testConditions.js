/*
  Test conditions on one scenario (world.md, Set the test conditions): a
  tool the scenario uses misbehaves on purpose. The run applies it to that
  scenario only and records it as an injected failure, never as an outage.

  The five kinds the backend runs (Karthik's review, 23 Nov, #22). A lost
  answer is its own kind, not a sub-choice of a timeout: the call ran — a
  write landed — and only the answer never came back.

  Saved on the scenario: row.testCondition = { tool, kind, call }, with
  call "first" (the default) | "every" | a call number (2, 3…).
*/
import { replicationOf } from "./replication";

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

/** Which call it hits: the first (default), every one, or call number N. */
export const CALLS = [
  { id: "first", label: "First call" },
  { id: "every", label: "Every call" },
  { id: "nth", label: "Call number…" },
];

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
  const call =
    c.call === "every"
      ? "every"
      : Number.isInteger(n) && n > 1
        ? n
        : "first";
  return { tool: c.tool, kind, call };
};

/** A kind that's no longer offered — kept as saved, shown under its old name. */
export const isRetiredKind = (kind) => !!RETIRED[kind];

export const callLabelOf = (call) =>
  call === "every"
    ? "every call"
    : typeof call === "number" && call > 1
      ? `call ${call}`
      : "first call";

/** A kind's name, offered or retired. */
export const kindLabelOf = (kind) =>
  CONDITIONS.find((x) => x.kind === kind)?.label || RETIRED[kind] || kind;

export const conditionLabelOf = (raw) => {
  const c = normalizeCondition(raw);
  if (!c) return null;
  return `${c.tool} · ${kindLabelOf(c.kind).toLowerCase()} · ${callLabelOf(c.call)}`;
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
