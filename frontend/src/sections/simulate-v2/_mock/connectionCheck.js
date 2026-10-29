/*
  Connecting a code or endpoint agent (roadmap connect.md, Week 1): how we
  start it and talk to it, then a connection check with one result per
  capability — it starts, it replies, it keeps the conversation, its tools
  reach the test world, it cancels — each with the repair when it fails. The
  check awards no business score. A connection is a draft until the check
  passes; after that it's ready, and "needs attention" when something it
  relied on (a credential) lapsed — the last good test stays visible.

  Stored on the agent's values: `connection` (the spec a person confirmed)
  and `connectionTest` ({ at, results, passed, productionTools }).
*/
import { sourceKindOf } from "./agentRegistry";
import { hashSeed } from "./runStream";

/* Agents we start or call ourselves — a platform or built-in agent has its
   own connection checks. */
export const checksConnection = (agent) =>
  ["repo", "upload", "framework", "endpoint"].includes(sourceKindOf(agent));

/* An agent we start from code needs a start command; one we only call
   already runs somewhere. */
export const startsFromCode = (agent) =>
  ["repo", "upload", "framework"].includes(sourceKindOf(agent));

/* Tools that write money or records — the ones a hard-coded production URL
   makes unsafe to test. */
const WRITES = /refund|charge|pay|cancel|book|transfer|create|update|delete/i;

/** The connection as confirmed, else what we read from the code. */
export const connectionSpecOf = (agent, found = {}) => {
  const saved = agent?.values?.connection || {};
  const endpoint = agent?.values?.endpoint || "";
  return {
    method: saved.method || "http",
    startCommand:
      saved.startCommand ??
      (startsFromCode(agent) ? found.runCommand || "" : ""),
    workdir: saved.workdir ?? ".",
    port: saved.port ?? (startsFromCode(agent) ? "8080" : ""),
    health: saved.health ?? (startsFromCode(agent) ? "/health" : ""),
    path: saved.path ?? (endpoint ? endpoint : "/chat"),
    messageField: saved.messageField ?? "message",
    /* Not read from code: the session field is what people most often have
       to confirm — until it's set, the check shows a conversation reset. */
    sessionField: saved.sessionField ?? "",
    replyField: saved.replyField ?? "reply",
    toolsRedirected: !!saved.toolsRedirected,
    observeOnly: saved.observeOnly || [],
    /* An async task (A2A-style): it answers with a job ID at once; progress,
       completion and an artifact come later. */
    taskIdField: saved.taskIdField ?? "job_id",
    progressPath: saved.progressPath ?? "/jobs/{id}/events",
    statusField: saved.statusField ?? "status",
    artifactField: saved.artifactField ?? "",
    cancelPath: saved.cancelPath ?? "",
  };
};

/* The tool a hard-coded production URL would still send to production —
   one write tool, picked the same way every time for this agent. */
const productionToolOf = (agent, tools = []) => {
  const writes = tools.filter((t) => WRITES.test(t));
  if (!writes.length) return null;
  return writes[hashSeed(`${agent?.key || agent?.name}:prod`) % writes.length];
};

/* An async task's checks: the acknowledgement is not the answer — the
   artifact is what a trial grades. */
export const ASYNC_CHECKS = [
  { id: "accepted", label: "Accepts a task" },
  { id: "progress", label: "Reports progress" },
  { id: "completion", label: "Finishes" },
  { id: "artifact", label: "Hands back an artifact" },
  { id: "recover", label: "Recovers without a second job" },
  { id: "cancel", label: "Cancels" },
];
export const checksFor = (spec) =>
  spec?.method === "async" ? ASYNC_CHECKS : CHECKS;

export const CHECKS = [
  { id: "starts", label: "Starts" },
  { id: "replies", label: "Replies" },
  { id: "continuity", label: "Keeps the conversation" },
  { id: "tools", label: "Tools reach the test world" },
  { id: "cancel", label: "Cancels" },
];

/**
 * The check, from the spec as it stands: [{ id, label, state, detail,
 * repair? }] with state "ok" | "failed" | "unavailable", plus the tools that
 * still reach production.
 */
export const runConnectionCheck = (agent, spec, tools = []) => {
  const fromCode = startsFromCode(agent);
  const out = [];
  const add = (id, state, detail, repair = null) =>
    out.push({
      id,
      label: checksFor(spec).find((c) => c.id === id).label,
      state,
      detail,
      ...(repair && { repair }),
    });
  if (spec.method === "async") return runAsyncCheck(spec, add, out);

  if (fromCode && !spec.startCommand.trim())
    add(
      "starts",
      "failed",
      "No start command — we don't guess one from a file name.",
      {
        id: "start",
        label: "Add the start command",
      },
    );
  else if (fromCode)
    add(
      "starts",
      "ok",
      `Started with ${spec.startCommand} in ${spec.workdir} — ready on :${spec.port || "8080"} (${spec.health || "/health"} answered 200) in 2.1 s.`,
    );
  else add("starts", "ok", `Reachable — ${spec.path} answered 200 in 340 ms.`);

  if (!spec.replyField.trim())
    add(
      "replies",
      "failed",
      "Connected, but no mapped reply — a 200 with nothing to show isn't a turn.",
      { id: "reply", label: "Pick the reply field" },
    );
  else
    add(
      "replies",
      "ok",
      `"Where is my order?" → "What is your order number?" — read from ${spec.replyField}.`,
    );

  if (!spec.sessionField.trim())
    add(
      "continuity",
      "failed",
      "The conversation resets every turn — turn 2 arrived without turn 1's session, so it asked for the order number again.",
      { id: "session", label: "Map the session ID" },
    );
  else
    add(
      "continuity",
      "ok",
      `Turn 2 kept ${spec.sessionField} = test-1 — it remembered the order number.`,
    );

  const prod = spec.toolsRedirected ? null : productionToolOf(agent, tools);
  const stillLive = prod && !spec.observeOnly.includes(prod) ? [prod] : [];
  if (stillLive.length)
    add(
      "tools",
      "failed",
      `${stillLive[0]} still reaches production — its URL is hard-coded, so scenarios that need it can't run against the test world.`,
      { id: "tools", label: "Point it at the test world" },
    );
  else
    add(
      "tools",
      "ok",
      `${tools[0] || "lookup_order"} answered with seeded order ORD-1042 — its tools reach the test world${spec.observeOnly.length ? `; ${spec.observeOnly.join(", ")} observed only` : ""}.`,
    );

  if (fromCode)
    add(
      "cancel",
      "ok",
      "Cancel stopped the turn in 0.4 s and the process exited cleanly.",
    );
  else
    add(
      "cancel",
      "unavailable",
      "Not declared — stopping a run waits for the current turn to finish.",
    );

  return {
    results: out,
    passed: out.every((r) => r.state !== "failed"),
    productionTools: stillLive,
  };
};

/* An async task, end to end: accepted with a job ID, progress, a terminal
   state, the artifact retrieved, a dropped watcher reconnected to the same
   job, and cancellation as declared. */
const runAsyncCheck = (spec, add, out) => {
  if (!spec.taskIdField.trim())
    add(
      "accepted",
      "failed",
      "It answered, but no job ID was read — we can't follow the task or avoid starting it twice.",
      { id: "taskId", label: "Set the job ID field" },
    );
  else
    add(
      "accepted",
      "ok",
      `Job accepted — job_7f3a, read from ${spec.taskIdField}, in 210 ms. The acknowledgement isn't graded.`,
    );
  if (!spec.progressPath.trim())
    add(
      "progress",
      "unavailable",
      "No progress events declared — a run waits for the job to finish.",
    );
  else
    add(
      "progress",
      "ok",
      `3 progress events from ${spec.progressPath} — 25%, 60%, 90%.`,
    );
  if (!spec.statusField.trim())
    add(
      "completion",
      "failed",
      "No terminal state read — we can't tell done from still running.",
      { id: "status", label: "Set the status field" },
    );
  else
    add("completion", "ok", `Finished in 41 s — ${spec.statusField} = done.`);
  if (!spec.artifactField.trim())
    add(
      "artifact",
      "failed",
      "Done, but no artifact read — a trial grades the artifact, not the acknowledgement.",
      { id: "artifact", label: "Set the artifact field" },
    );
  else
    add(
      "artifact",
      "ok",
      `Retrieved refund-summary.pdf (42 KB) from ${spec.artifactField} — what the trial grades.`,
    );
  add(
    "recover",
    "ok",
    "Dropped the watcher mid-job and reconnected: the same job_7f3a, no second job started.",
  );
  if (spec.cancelPath.trim())
    add(
      "cancel",
      "ok",
      `Cancel at ${spec.cancelPath} confirmed — the job ended as cancelled and its partial output was kept.`,
    );
  else
    add(
      "cancel",
      "unavailable",
      "Not declared — a stopped run waits for the job to finish.",
    );
  return {
    results: out,
    passed: out.every((r) => r.state !== "failed"),
    productionTools: [],
  };
};

/**
 * Where the connection stands: { state: "draft" | "ready" | "attention",
 * label, line, at? }. Only for agents whose connection we check.
 */
export const connectionStateOf = (agent) => {
  const t = agent?.values?.connectionTest;
  const lapsed = !!agent?.values?.disconnected;
  if (t?.passed && lapsed)
    return {
      state: "attention",
      label: "Needs attention",
      line: `A credential it uses stopped working. Last good test ${fmt(t.at)} — replace it and test again.`,
      at: t.at,
    };
  if (t?.passed)
    return {
      state: "ready",
      label: "Ready",
      line: `Tested ${fmt(t.at)}.`,
      at: t.at,
    };
  return {
    state: "draft",
    label: "Draft",
    line: t
      ? "The last test failed — fix what it found and test again. Settings are kept."
      : "Saved as a draft — test the connection before a run.",
  };
};

const fmt = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-GB", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
