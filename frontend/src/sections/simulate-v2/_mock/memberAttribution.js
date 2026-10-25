/*
  Per-member results for an agent made of several agents — a Vapi squad, a
  Retell conversation flow, a code app with handoffs.

  Every conversation starts at the entry member (the first one: a squad's
  router, a flow's start node). Most are handed to one specialist; some are
  settled at the entry. A conversation — and whether it passed — is credited
  to the member it ended with: that's who held it when it went right or wrong.

  In a real run the path comes from the trace (each member's turns are its own
  agent spans). Here it comes from the scenario's `member` when it names one,
  else a stable hash, so the same run always reads the same.
*/
import { membersOf } from "./improvementPolicy";

const hash = (s) => {
  let h = 0x811c9dc5;
  const str = String(s || "");
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
};

const outcomeOf = (t) =>
  t.status === "passed"
    ? "passed"
    : t.status === "failed" || t.status === "flaky"
      ? "failed"
      : "other";

/** The members a conversation went through, entry first. */
export const memberPathOf = (task, members) => {
  if (members.length < 2) return members.slice(0, 1);
  const [entry, ...rest] = members;
  const named = task.member || task.scenario?.member;
  const pick = named && rest.find((m) => m.id === named || m.name === named);
  if (pick) return [entry, pick];
  const h = hash(`path:${task.scenarioId || task.id}`);
  /* About one in six is settled at the entry without a handoff. */
  if (h % 6 === 0) return [entry];
  return [entry, rest[h % rest.length]];
};

/**
 * Rows for the By member table, entry first:
 * { member, entry, reached, ended, passed, failed, handedOff, topCause }.
 */
export const memberResultsOf = ({
  agent,
  tasks = [],
  causeOf = () => null,
}) => {
  const members = membersOf(agent);
  if (members.length < 2) return null;
  const rows = new Map(
    members.map((m, i) => [
      m.id,
      {
        member: m,
        entry: i === 0,
        reached: 0,
        ended: 0,
        passed: 0,
        failed: 0,
        handedOff: 0,
        causes: new Map(),
      },
    ]),
  );
  let handoffs = 0;
  tasks
    .filter((t) => !t.pulled)
    .forEach((t) => {
      const path = memberPathOf(t, members);
      path.forEach((m) => {
        rows.get(m.id).reached += 1;
      });
      if (path.length > 1) {
        handoffs += 1;
        rows.get(path[0].id).handedOff += 1;
      }
      const last = rows.get(path[path.length - 1].id);
      last.ended += 1;
      const o = outcomeOf(t);
      if (o === "passed") last.passed += 1;
      if (o === "failed") {
        last.failed += 1;
        const c = causeOf(t);
        if (c) last.causes.set(c, (last.causes.get(c) || 0) + 1);
      }
    });
  const list = [...rows.values()].map((r) => {
    const top = [...r.causes.entries()].sort((a, b) => b[1] - a[1])[0];
    return {
      ...r,
      topCause: top ? { label: top[0], count: top[1] } : null,
    };
  });
  return { rows: list, handoffs, total: tasks.filter((t) => !t.pulled).length };
};
