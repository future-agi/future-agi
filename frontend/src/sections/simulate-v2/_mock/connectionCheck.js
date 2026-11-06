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
import { definitionOf, sourceKindOf } from "./agentRegistry";
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
    /* Whether a cancel was seen to stop the agent. Declared isn't observed:
       until it was, the step reads Stop requested or Cleanup failed. */
    /* We see an agent we start stop; a hosted endpoint we can only ask. */
    stopObserved: saved.stopObserved ?? startsFromCode(agent),
  };
};

/**
 * The values a finished check saves. Checks that failed before (in the last
 * result, or the one a fix cleared) and pass now are kept as fixed, so the
 * report can say "Fixed · was Blocked" instead of the problem just vanishing.
 */
export const checkSavedPatch = (values, out, at = new Date().toISOString()) => {
  const prev = values?.connectionTestBefore || values?.connectionTest || null;
  const now = Object.fromEntries(out.results.map((r) => [r.id, r]));
  const newly = (prev?.results || [])
    .filter((r) => r.state === "failed" && now[r.id]?.state === "ok")
    .map((r) => ({ id: r.id, label: r.label, at, was: r.detail }));
  const kept = (values?.fixedChecks || []).filter(
    (f) => now[f.id]?.state === "ok" && !newly.some((n) => n.id === f.id),
  );
  return {
    connectionTest: { at, ...out },
    connectionTestBefore: null,
    fixesApplied: null,
    fixedChecks: [...kept, ...newly],
  };
};

/**
 * A changed connection clears its check, keeping what it found for the next,
 * and which repair was applied (a check's repair id, or "all" for an edit
 * that could touch anything) — only those read "Fix applied"; the rest stay
 * as the last check found them.
 */
export const clearedCheckPatch = (values, applied = "all") => {
  const prev = values?.connectionTest ? [] : values?.fixesApplied || [];
  return {
    connectionTest: null,
    connectionTestBefore:
      values?.connectionTest || values?.connectionTestBefore || null,
    fixesApplied:
      applied === "all" || prev === "all"
        ? "all"
        : [...new Set([...prev, applied])],
  };
};

/*
  Repairs small enough to make where the problem shows (the run plan): one
  connection field, or pointing the tools at the test world. Anything else
  is done on the agent's Connections tab.
*/
const INLINE_FIELDS = {
  start: ["startCommand", "Start command"],
  reply: ["replyField", "Reply field"],
  session: ["sessionField", "Session ID field"],
  taskId: ["taskIdField", "Job ID field"],
  status: ["statusField", "Status field"],
  artifact: ["artifactField", "Artifact field"],
};

/** { kind: "field", label, value } | { kind: "tools", label } | null */
export const inlineRepairOf = (spec, fixId) => {
  if (fixId === "tools")
    return { kind: "tools", label: "Point its tools at the test world" };
  const f = INLINE_FIELDS[fixId];
  return f ? { kind: "field", label: f[1], value: spec?.[f[0]] || "" } : null;
};

/** The agent values an inline repair saves — the check runs again after. */
export const inlineRepairPatch = (values, spec, fixId, value = "") =>
  fixId === "tools"
    ? {
        connection: { ...spec, toolsRedirected: true },
        ...clearedCheckPatch(values, "tools"),
      }
    : {
        connection: { ...spec, [INLINE_FIELDS[fixId][0]]: value.trim() },
        ...clearedCheckPatch(values, fixId),
      };

/* The tool a hard-coded production URL would still send to production —
   one write tool, picked the same way every time for this agent. */
const productionToolOf = (agent, tools = []) => {
  /* Reading the code found a hard-coded production URL on this tool. */
  const named = (agent?.definition?.hardCodedTools || []).find((t) =>
    tools.includes(t),
  );
  if (named) return named;
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
/*
  A cancel the agent never confirmed (connect.md): "Stop requested" until it
  is observed stopped, or "Cleanup failed" when it didn't — never ok on
  metadata alone. Which one is fixed per agent. Stop requested keeps the
  "unavailable" state (neither green nor a repairable failure) with an
  `outcome` the check's `passed` reads.
*/
/* A stop we can't watch (a hosted endpoint) reads Stop requested until the
   agent is seen to stop; Cleanup failed only when cleanup is seen to fail
   (connect.md). Neither fails the connection on its own. */
const unobservedStop = (spec) =>
  spec.cleanupFailed
    ? {
        state: "failed",
        outcome: "cleanup-failed",
        detail: "Cleanup failed — the agent didn't stop.",
      }
    : {
        state: "unavailable",
        outcome: "stop-requested",
        detail: "Stop requested — waiting to see the agent stop.",
      };
const checkPassed = (out) => out.every((r) => r.state !== "failed");

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
  const add = (id, state, detail, repair = null, outcome = null, extra = {}) =>
    out.push({
      id,
      label: checksFor(spec).find((c) => c.id === id).label,
      state,
      detail,
      ...(repair && { repair }),
      ...(outcome && { outcome }),
      ...extra,
    });
  /* A secret the code reads that was left unset at connect: the process
     exits on start. connect.md: show the exit and a redacted log excerpt. */
  const unsetKey = (definitionOf(agent)?.credentialKeys || []).find(
    (k) => k && typeof k === "object" && k.unset && !k.ref,
  )?.name;
  const addStop = (declared, okDetail, noneDetail) => {
    if (!declared) return add("cancel", "unavailable", noneDetail);
    if (spec.stopObserved) return add("cancel", "ok", okDetail);
    const u = unobservedStop(spec);
    add("cancel", u.state, u.detail, null, u.outcome);
  };
  if (spec.method === "async") return runAsyncCheck(spec, addStop, add, out);

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
  else if (fromCode && unsetKey)
    add(
      "starts",
      "failed",
      `Exited with code 1 before :${spec.port || "8080"} answered — ${unsetKey} isn't set.`,
      { id: "secret", label: `Set ${unsetKey}` },
      null,
      {
        exitCode: 1,
        log: [
          `$ ${spec.startCommand}`,
          "Traceback (most recent call last):",
          '  File "app/config.py", line 14, in <module>',
          `    API_KEY = os.environ["${unsetKey}"]`,
          `KeyError: '${unsetKey}'`,
          "process exited with code 1",
        ],
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
    /* Reply not found: show the probe's response (secrets redacted) and the
       field it most likely sits in — a mapping, not an annotation. */
    add(
      "replies",
      "failed",
      "Connected, but no reply was read — the response has its text under answer.",
      { id: "replySuggest", label: "Use answer" },
      null,
      {
        probe: {
          answer: "What is your order number?",
          session_id: "test-1",
          usage: { tokens: 41 },
          api_key: "••••••••",
        },
        suggest: { replyField: "answer" },
      },
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
  else {
    /* A redirected tool is the proof the fix needs — name it, not another. */
    const redirected = spec.toolsRedirected
      ? productionToolOf(agent, tools)
      : null;
    add(
      "tools",
      "ok",
      `${
        redirected
          ? `${redirected} now lands in the test world, not production`
          : `${tools[0] || "lookup_order"} answered with seeded order ORD-1042`
      } — its tools reach the test world${spec.observeOnly.length ? `; ${spec.observeOnly.join(", ")} observed only` : ""}.`,
    );
  }

  addStop(
    fromCode,
    "Cancel stopped the turn in 0.4 s and the process exited cleanly.",
    "Not declared — stopping a run waits for the current turn to finish.",
  );

  return {
    results: out,
    passed: checkPassed(out),
    productionTools: stillLive,
  };
};

/* An async task, end to end: accepted with a job ID, progress, a terminal
   state, the artifact retrieved, a dropped watcher reconnected to the same
   job, and cancellation as declared. */
const runAsyncCheck = (spec, addStop, add, out) => {
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
  addStop(
    !!spec.cancelPath.trim(),
    `Cancel at ${spec.cancelPath} confirmed — the job ended as cancelled and its partial output was kept.`,
    "Not declared — a stopped run waits for the job to finish.",
  );
  return {
    results: out,
    passed: checkPassed(out),
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
    /* Not "Draft" — beside "v1 · in production" that reads as a draft agent. */
    label: t ? "Test failed" : "Not tested",
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

/**
 * The last probe, as it can be shown: the request and reply with secrets as
 * ••••, personal data masked, and the tool spans seen while it ran.
 *   { request, response, spans: [{ name, ok, ms }] }
 */
export const probeOf = (agent, spec, tools = [], results = []) => {
  const state = (id) => results.find((r) => r.id === id)?.state;
  const sent = {
    [spec.messageField || "message"]: "Where is my order?",
    ...(spec.sessionField && { [spec.sessionField]: "test-1" }),
    customer: {
      name: "M•••• K••••",
      email: "m••••@••••.com",
      phone: "+•• •••• ••34",
    },
  };
  const replied =
    state("replies") === "failed"
      ? {}
      : { [spec.replyField || "reply"]: "What is your order number?" };
  const prod = productionToolOf(agent, tools);
  const spans = [
    { name: "agent.turn", ok: true, ms: 2100 },
    { name: tools[0] || "lookup_order", ok: true, ms: 180 },
    ...(state("tools") === "failed" && prod
      ? [{ name: prod, ok: false, ms: 95 }]
      : []),
  ];
  return {
    request: {
      method: "POST",
      url: spec.path || "/chat",
      headers: { authorization: "Bearer ••••", "x-api-key": "••••" },
      body: sent,
    },
    response: {
      status: state("starts") === "failed" ? 0 : 200,
      ms: 340,
      body: replied,
    },
    spans,
  };
};
