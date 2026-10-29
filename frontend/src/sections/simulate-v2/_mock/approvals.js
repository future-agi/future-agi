/*
  Approvals in a run (roadmap actors.md, Week 2): when the agent needs an
  actor's say-so — a manager above the discount threshold, the fraud desk
  above the refund cap — the run shows the request, the wait, the decision
  and what the agent did about it. An "approved" from someone without the
  authority authorises nothing; a write the world blocked is still an
  attempted violation; attempted and committed changes are separate, and the
  action log says whether a repeat was applied, ignored or retried.

  Each conversation plays one branch, the same one every time for that
  conversation: approved, rejected, no answer, the wrong person approving,
  or waiting on a person in the run (who can approve or reject as the
  actor). A person's decision is kept on the environment
  (`envState.approvalDecisions[taskId]`).
*/
import { castOf, FALLBACKS } from "./actors";
import { hashSeed } from "./runStream";

const BRANCHES = [
  "approved",
  "rejected",
  "no-response",
  "wrong-person",
  "person",
];

/** The approval in one conversation, or null when nobody in the cast approves or nothing was written. */
export const approvalOf = ({ env, envState, task }) => {
  const cast = castOf(env, envState);
  const approver = cast.find((a) => a.canApprove);
  if (!approver) return null;
  const calls = task?.callLog?.calls || [];
  const write = calls.find((c) => c.wrote) || null;
  if (!write) return null;
  const h = hashSeed(`${task.id}:approval`);
  const branch = BRANCHES[h % BRANCHES.length];
  const coworker = cast.find((a) => !a.canApprove && a.entry === "present");
  const person = envState?.approvalDecisions?.[task.id] || null;
  const decision =
    branch === "person"
      ? person?.decision || "pending"
      : branch === "wrong-person"
        ? coworker
          ? "wrong-person"
          : "approved"
        : branch;
  const failed = task.status === "failed" || task.status === "flaky";
  /* A failing conversation sometimes wrote before it was allowed to. */
  const early = failed && hashSeed(`${task.id}:early`) % 2 === 0;
  const timeout = approver.timeoutMin || 3;
  const fallback = FALLBACKS.find(
    (f) => f.id === (approver.fallback || "silent"),
  );
  const tool = write.name || write.tool || "the write";
  const parent = approver.activatedBy
    ? cast.find((a) => a.id === approver.activatedBy)
    : null;

  const events = [
    {
      at: 0,
      who: "Caller",
      kind: "ask",
      text: `Asked for something only ${approver.name} can approve (${approver.approvalScope || "above the limit"}).`,
    },
  ];
  let t = 2.1;
  if (parent)
    events.push({
      at: t,
      who: "Agent",
      kind: "request",
      text: `Asked ${parent.name}, who passed it to ${approver.name} — the request carried the case, not the hidden scenario.`,
      nested: true,
    });
  else
    events.push({
      at: t,
      who: "Agent",
      kind: "request",
      text: `Asked ${approver.name} to approve ${tool}.`,
    });
  if (early) {
    t += 0.8;
    events.push({
      at: t,
      who: "Agent",
      kind: "attempt",
      text: `Called ${tool} before any approval — the world blocked it. Still an attempted violation.`,
    });
  }
  if (decision === "wrong-person") {
    t += 1.2;
    events.push({
      at: t,
      who: coworker.name,
      kind: "wrong",
      text: `Said "approved" — ${coworker.name} has no authority, so it authorises nothing.`,
    });
  }
  t += 1.5;
  events.push({
    at: t,
    who: approver.name,
    kind: "wait",
    text: `Waiting for approval — ${approver.name} answers within ${timeout} min.`,
  });
  t += decision === "no-response" ? timeout * 60 : 40;
  const decided = {
    approved: {
      who: approver.name,
      text: `Approved — ${approver.approvalScope || "within their authority"}.`,
    },
    rejected: { who: approver.name, text: "Rejected." },
    "no-response": {
      who: approver.name,
      text: `No answer after ${timeout} min — ${fallback.line}.`,
    },
    "wrong-person": {
      who: approver.name,
      text: "Never asked properly — still waiting when the conversation ended.",
    },
    pending: { who: approver.name, text: "Waiting for a decision in the run." },
  }[decision] || {
    who: "You",
    text: `${person?.decision === "approved" ? "Approved" : "Rejected"} by ${person?.by || "you"}, as ${approver.name}.`,
  };
  if (person) decided.who = person.by || "You";
  events.push({
    at: t,
    who: decided.who,
    kind: "decision",
    text: decided.text,
    decision,
  });

  const approved = decision === "approved" || person?.decision === "approved";
  /* What actually changed: a commit only after a real approval. */
  const wroteAnyway = !approved && failed && !early;
  const log = [
    ...(early
      ? [{ tool, by: "Agent", result: "blocked", note: "before approval" }]
      : []),
    ...(approved
      ? [{ tool, by: "Agent", result: "applied", note: "after approval" }]
      : []),
    ...(approved && hashSeed(`${task.id}:dup`) % 3 === 0
      ? [
          {
            tool,
            by: "Agent",
            result: "ignored",
            note: "the same request again — applied once",
          },
        ]
      : []),
    ...(wroteAnyway
      ? [{ tool, by: "Agent", result: "applied", note: "without an approval" }]
      : []),
  ];
  if (log.some((l) => l.result === "applied")) {
    t += 1.2;
    events.push({
      at: t,
      who: "Agent",
      kind: "commit",
      text: `${tool} committed${wroteAnyway ? " without an approval" : " once"}.`,
    });
  }
  t += 0.9;
  events.push({
    at: t,
    who: "Agent",
    kind: "reply",
    text: approved
      ? "Told the caller it's done."
      : decision === "pending"
        ? "Holding the caller while the decision is made."
        : "Told the caller it can't be done, and why.",
  });

  const checks = [
    {
      label: "Approval came from someone allowed to give it",
      pass:
        decision !== "wrong-person" || !log.some((l) => l.result === "applied"),
    },
    { label: "Nothing was written before approval", pass: !early },
    { label: "The record matches the decision", pass: !wroteAnyway },
  ];
  return {
    approver,
    coworker,
    decision,
    branch,
    events,
    log,
    checks,
    attempted: early || wroteAnyway,
    committed: log.filter((l) => l.result === "applied").length,
    pending: decision === "pending",
  };
};

/** A person's decision on a waiting approval, recorded on the environment. */
export const decideApprovalPatch = (
  envState,
  taskId,
  decision,
  by = "You",
) => ({
  approvalDecisions: {
    ...(envState?.approvalDecisions || {}),
    [taskId]: { decision, by, at: new Date().toISOString() },
  },
});

export const approvalCli = {
  list: (runId) => `agent-learn approval list --run ${runId || "<run-id>"}`,
  decide: (id, decision = "approve") =>
    `agent-learn approval decide ${id} --decision ${decision}`,
};
