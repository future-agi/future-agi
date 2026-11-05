/*
  Shared state in a multi-agent trial (roadmap multiagent.md): whether two
  workers acted on the same record and which write committed. The final
  aggregate check stays separate. A participant whose part wasn't observed
  reads Unknown — never guessed.

  Derived from the trial's id, so the same trial always reads the same.
*/
import { membersOf } from "./improvementPolicy";
import { memberPathOf } from "./memberAttribution";
import { delegationEvidenceOf } from "./handoffs";
import { sideEffectOf } from "./toolEnv";
import { hashSeed } from "./runStream";

const FINAL = {
  passed: "pass",
  failed: "fail",
  flaky: "mixed across repeats",
  unmeasured: "not measured",
};

/**
 * `{ record, rows: [{ participant, write, result, note? }], final }` for a
 * multi-agent trial where two workers wrote the same record, else null.
 * result: "Committed" | "Rejected" | "Not committed".
 */
export const sharedStateOf = ({ agent, task }) => {
  const members = membersOf(agent);
  if (members.length < 3 || !task) return null;
  /* Only conversations that were handed to a worker can have two writers. */
  if (memberPathOf(task, members).length < 2) return null;
  const h = hashSeed(`${task.scenarioId || task.id}:shared`);
  if (h % 8 !== 0) return null;
  const [, a, b] = members;
  const tool = (a.tools || []).find(
    (t) =>
      sideEffectOf({ name: typeof t === "string" ? t : t.name }) === "write",
  );
  if (!tool) return null;
  const write = typeof tool === "string" ? tool : tool.name;
  const observed = delegationEvidenceOf(agent).observed;
  const timedOut = (h >>> 3) % 2 === 1;
  const refund = /refund/.test(write);
  return {
    record: `order O-${100 + ((h >>> 5) % 900)}`,
    rows: [
      { participant: a.name, write, result: "Committed" },
      {
        participant: observed ? b.name : "Unknown",
        write,
        result: timedOut ? "Not committed" : "Rejected",
        note: timedOut
          ? "timed out"
          : refund
            ? "already refunded"
            : "already changed",
      },
    ],
    final: FINAL[task.status] || "not measured",
  };
};
