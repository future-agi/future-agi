/*
  What world actors did in a conversation (PR #3698): an actor's action shows
  in the transcript at the time it happened, as one event — "Manager ·
  approver", what they did, and how it went (Committed, Refused or Failed).
  The raw arguments, result and error sit folded behind it.

  Mocked from the conversation's id, so a conversation always reads the same.
  An approver's event follows the approval the run already played (approvals.js);
  any other world actor with an action this world has acts once, at a fixed
  point in the conversation.
*/
import { castOf, sideOf, roleOf, actorActionsOf, FALLBACKS } from "./actors";
import { sideEffectOf } from "./toolEnv";
import { approvalOf } from "./approvals";
import { hashSeed } from "./runStream";

export const EVENT_OUTCOMES = ["Committed", "Refused", "Failed"];
export const isBadOutcome = (o) => o === "Refused" || o === "Failed";

const where = (h, count) => {
  /* Past the opening exchange, never after the last turn. */
  const lo = Math.min(1, count - 1);
  const hi = Math.max(lo, count - 2);
  return lo + (h % (hi - lo + 1));
};

/**
 * The world actors' events in one conversation, in time order:
 *   [{ id, after, actorId, name, role, action, outcome, args, result, error }]
 * `after` is the index of the turn it follows.
 */
export const actorEventsOf = ({ env, envState, task }) => {
  const count = (task?.steps || []).length;
  if (count < 2) return [];
  const world = castOf(env, envState).filter((a) => sideOf(a) === "world");
  const approval = approvalOf({ env, envState, task });
  const events = [];

  world.forEach((actor) => {
    const known = actorActionsOf(actor, env, envState).filter((a) => a.known);
    const h = hashSeed(`${task.id}:${actor.id}:event`);
    const base = {
      actorId: actor.id,
      name: actor.name,
      role: roleOf(actor),
    };

    if (approval && approval.approver.id === actor.id) {
      /* Approval is for an action that changes something — never a read. */
      const isWrite = (n) => !!n && sideEffectOf({ name: n }) === "write";
      const calls = task.callLog?.calls || [];
      const tool =
        calls.map((c) => c.name || c.tool).find(isWrite) ||
        (env?.tools || []).map((t) => t.name).find(isWrite) ||
        "the change";
      const action =
        known.find((a) => a.name === `approve_${tool}`)?.name ||
        `approve_${tool}`;
      const timeout = actor.timeoutMin || 3;
      const fallback = FALLBACKS.find(
        (f) => f.id === (actor.fallback || "silent"),
      );
      const outcome = {
        approved: "Committed",
        rejected: "Refused",
        "no-response": "Failed",
      }[approval.decision];
      if (!outcome) return;
      events.push({
        ...base,
        id: `${task.id}:${actor.id}`,
        after: where(h, count),
        action,
        outcome,
        args: { action, request: tool, scope: actor.approvalScope || null },
        result:
          outcome === "Committed"
            ? { status: "approved", by: actor.id }
            : outcome === "Refused"
              ? { status: "rejected", by: actor.id }
              : null,
        error:
          outcome === "Failed"
            ? `No answer after ${timeout} min — ${fallback.line}`
            : null,
      });
      return;
    }

    if (!known.length) return;
    const action = known[h % known.length].name;
    const roll = (h >>> 8) % 10;
    const outcome = roll < 7 ? "Committed" : roll < 9 ? "Refused" : "Failed";
    events.push({
      ...base,
      id: `${task.id}:${actor.id}`,
      after: where(h, count),
      action,
      outcome,
      args: { action, task: task.id },
      result:
        outcome === "Committed"
          ? { status: "ok", by: actor.id }
          : outcome === "Refused"
            ? { status: "refused", by: actor.id }
            : null,
      error:
        outcome === "Failed" ? `${action} did not complete — timed out` : null,
    });
  });

  return events.sort((a, b) => a.after - b.after);
};
