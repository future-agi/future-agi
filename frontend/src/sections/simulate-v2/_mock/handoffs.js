/*
  A multi-agent app's coordinator and workers in a run (roadmap connect.md,
  Week 2): who handed what to whom, what each worker did in the world, and
  what came back — only as far as the evidence goes. Declared workers (what
  the app or platform says it has) are not proof that they ran; a worker's
  results are facts only when its delegation and return events were observed.
  Without them, per-worker results are unmeasured and only the overall
  result counts.

  Real child workers are the agent's own; the simulated caller and the
  scenario's actors are the world's, and never appear here.
*/
import { sourceKindOf } from "./agentRegistry";
import { membersOf } from "./improvementPolicy";
import { memberPathOf } from "./memberAttribution";
import { hashSeed } from "./runStream";

/**
 * Whether this agent's runs carry delegation and return events:
 *   { observed, source, line }
 * Code apps are instrumented; hosted squads and flows report transfers and
 * node changes. An agent we only call by URL sends nothing about its insides.
 */
export const delegationEvidenceOf = (agent) => {
  const kind = sourceKindOf(agent);
  const off = agent?.values?.delegationEvents === false;
  if (kind === "endpoint" || off)
    return {
      observed: false,
      source: "declared",
      line: "Its workers are declared, not observed — runs get no delegation or return events from it, so per-worker results are unmeasured and only the overall result counts.",
    };
  const platform = kind === "platform";
  return {
    observed: true,
    source: platform ? "provider" : "instrumented",
    line: platform
      ? "Its platform reports every transfer, so each worker's part is observed in runs."
      : "Its code emits delegation and return events, so each worker's part is observed in runs.",
  };
};

/* The tools a worker may use — its own list, else the agent's tools split by
   what its role says it does. */
export const workerToolsOf = (member, tools = []) => {
  if (member.tools?.length)
    return member.tools.map((t) => (typeof t === "string" ? t : t.name));
  const words =
    String(`${member.name} ${member.role || ""}`)
      .toLowerCase()
      .match(/[a-z]{4,}/g) || [];
  return tools.filter((t) =>
    words.some((w) => t.toLowerCase().includes(w.slice(0, 5))),
  );
};

/**
 * One conversation's handoffs:
 *   { members, evidence, events: [{ at, kind, from, to, text, attempted? }],
 *     outcome: "returned" | "timeout" | "settled", failedWorker? }
 * kind: "receive" | "delegate" | "tool" | "return" | "timeout" | "reply".
 */
export const handoffsOf = ({ agent, task }) => {
  const members = membersOf(agent);
  if (members.length < 2) return null;
  const evidence = delegationEvidenceOf(agent);
  const path = memberPathOf(task, members);
  const [coord, worker] = path;
  const calls = task.callLog?.calls || [];
  const events = [
    {
      at: 0,
      kind: "receive",
      from: coord,
      text: `${coord.name} received the caller's request.`,
    },
  ];
  if (!worker) {
    events.push({
      at: 1.8,
      kind: "reply",
      from: coord,
      text: `${coord.name} settled it itself — no worker was involved.`,
    });
    return { members, evidence, events, outcome: "settled" };
  }
  const failed = task.status === "failed" || task.status === "flaky";
  const timeout = failed && hashSeed(`${task.id}:worker-timeout`) % 3 === 0;
  let t = 2.4;
  events.push({
    at: t,
    kind: "delegate",
    from: coord,
    to: worker,
    text: `${coord.name} handed the request to ${worker.name}.`,
  });
  calls
    .slice(0, timeout ? Math.max(1, calls.length - 1) : calls.length)
    .forEach((c) => {
      t += 1.1;
      events.push({
        at: t,
        kind: "tool",
        from: worker,
        text: `${worker.name} called ${c.name || c.tool || "a tool"}.`,
      });
    });
  if (timeout) {
    const last = calls[calls.length - 1];
    events.push({
      at: t + 30,
      kind: "timeout",
      from: worker,
      attempted: last?.name || last?.tool || null,
      text: `${worker.name} timed out after 30 s — no return event.${last ? ` Its ${last.name || last.tool} was attempted, not committed: the world shows no change.` : ""}`,
    });
    events.push({
      at: t + 31,
      kind: "reply",
      from: coord,
      text: `${coord.name} answered the caller without ${worker.name}'s result.`,
    });
    return {
      members,
      evidence,
      events,
      outcome: "timeout",
      failedWorker: worker,
    };
  }
  t += 1.4;
  events.push({
    at: t,
    kind: "return",
    from: worker,
    to: coord,
    text: `${worker.name} returned its result to ${coord.name}.`,
  });
  events.push({
    at: t + 0.9,
    kind: "reply",
    from: coord,
    text: `${coord.name} told the caller the outcome.`,
  });
  return { members, evidence, events, outcome: "returned" };
};
