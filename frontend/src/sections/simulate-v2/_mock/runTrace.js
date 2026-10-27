/*
  One scenario's trace, as spans (PRD v6, Story 11 — AC-11.3, 11.7, 11.9, 11.10).

  Built from the trajectory the run already recorded — the steps, the call log,
  the graders' verdicts — so the waterfall and the transcript can't disagree.
  Parent → child: the session, then each turn (the caller's and the agent's),
  inside an agent turn the LLM calls, the tool calls between them, and a guard
  span wherever a rule is checked. Every span sits on one time axis.

  A failed scenario pins to the span where it went wrong (`offendingId`), and
  the graders' scores hang on the span they grade where that can be told: a
  policy verdict on the guard that checked the rule, a task verdict on the
  closing turn, a leak on the turn that leaked.

  Deterministic: the same task in, the same spans out.
*/
import { hashSeed } from "./runStream";
import { subGoalResultsOf } from "./scenarioSubGoals";
import { tierOf, toolSettingOf } from "./toolEnv";

const CONVERSATIONAL = new Set(["voice", "chat", "multi"]);

const stageOf = (env) => {
  const s = env?.surface;
  if (s === "voice" || s === "chat" || s === "multi") return s;
  return s || "tools";
};

/* A tool's fidelity tier as the run pinned it. A connect-only world has no
   sandbox of ours — the agent's own systems answered. */
const fidelityOf = (name, env, envState) => {
  if (env?.worldMode === "connect-only")
    return { id: "agent", label: "Agent's own system" };
  const known = (env?.tools || []).find((t) => t.name === name) || { name };
  const setting = toolSettingOf(known, envState);
  return {
    id: setting.tier,
    label: tierOf(setting.tier).label,
    faults: setting.faults,
  };
};

/* Which rule a scenario is about, when one is checked. */
const ruleFor = (task, crux, env) => {
  const rules = env?.rules || [];
  if (crux && /^holds the line:\s*/i.test(crux.text))
    return crux.text.replace(/^holds the line:\s*/i, "");
  if (crux && /^verify who/i.test(crux.text))
    return "Verify the caller's identity before touching account state";
  if (crux && /^refuse/i.test(crux.text))
    return (
      rules[hashSeed(String(task.id)) % Math.max(1, rules.length)] || crux.text
    );
  if (task.critical && rules.length)
    return rules[hashSeed(`${task.id}:rule`) % rules.length];
  return null;
};

const round = (n, d = 0) => {
  const k = 10 ** d;
  return Math.round(n * k) / k;
};

/*
  Spread the call log over the agent's turns. The first agent turn is the
  greeting and calls nothing; the rest take the calls in order.
*/
const assignCalls = (agentIdx, calls) => {
  const out = new Map();
  if (!agentIdx.length || !calls.length) return out;
  const pool = agentIdx.length > 1 ? agentIdx.slice(1) : agentIdx;
  calls.forEach((c, j) => {
    const at =
      pool[
        Math.min(
          pool.length - 1,
          Math.floor(((j + 1) * pool.length) / (calls.length + 1)),
        )
      ];
    out.set(at, [...(out.get(at) || []), c]);
  });
  return out;
};

const argsText = (args) =>
  args && Object.keys(args).length ? JSON.stringify(args) : "{}";

/**
 * The trace of one task. `{ spans, total, offendingId, sessionId, model }`.
 * Each span: { id, parentId, depth, kind, name, start, end, status, note,
 *   tokens?, cost?, model?, input?, output?, tool?, guard?, evals[], offending }
 * kind: session | turn | caller | actor | llm | tool | guard | missing
 */
export const traceOf = (task, env, envState) => {
  if (!task) return { spans: [], total: 0, offendingId: null, sessionId: null };
  const stage = stageOf(env);
  const conversational = CONVERSATIONAL.has(stage);
  const steps = task.steps || [];
  const log = task.callLog || { calls: [], missing: [] };
  const model = envState?.agent?.values?.model || env?.model || "gpt-4.1";
  const fault = task.fault || {};
  const unmeasured = task.status === "unmeasured" || task.measured === false;
  const failedRun = !unmeasured && task.status !== "passed";

  /* Where it went wrong — the step the run recorded, moved onto the agent's
     side when it landed on the caller's. */
  let failAt =
    failedRun && typeof task.failStep === "number"
      ? Math.min(task.failStep, steps.length - 1)
      : null;
  if (failAt != null && conversational) {
    const next = steps.findIndex((s, i) => i >= failAt && s.role === "agent");
    const prev = [...steps.keys()]
      .reverse()
      .find((i) => i < failAt && steps[i].role === "agent");
    failAt = next >= 0 ? next : prev ?? failAt;
  }

  const { items } = subGoalResultsOf(task, env, envState);
  const crux =
    items.find((x) => x.mark === "critical" && x.status !== "met") || null;
  const rule = ruleFor(task, crux, env);

  const sessionId = `${task.id}::session`;
  const spans = [];
  let t = 0;

  /* A transport drop cuts the session short — the last turns never happened. */
  const cut = fault.transport
    ? Math.max(2, Math.ceil(steps.length * 0.6))
    : steps.length;
  const shown = steps.slice(0, cut);

  const agentIdx = conversational
    ? shown.map((s, i) => (s.role === "agent" ? i : -1)).filter((i) => i >= 0)
    : shown.map((s, i) => (s.role === "actor" ? -1 : i)).filter((i) => i >= 0);
  /* A browser agent drives one tool — the browser — one action at a time. */
  const browserTool =
    stage === "browser"
      ? (env?.tools || []).find((x) => /browser/i.test(x.name))?.name ||
        "browser"
      : null;
  const callsAt = conversational
    ? assignCalls(agentIdx, log.calls || [])
    : new Map();
  const lastAgent = agentIdx[agentIdx.length - 1];

  /* Tokens and cost are the task's own, spread over its LLM calls by time. */
  const llmShare = [];

  shown.forEach((st, i) => {
    const d = Math.max(200, st.duration || 800);
    const start = t;
    const end = t + d;
    t = end;
    const turnId = `${task.id}::t${i}`;
    const isAgent = conversational ? st.role === "agent" : true;
    const offendingTurn = failAt === i;

    if ((conversational && st.role !== "agent") || st.role === "actor") {
      spans.push({
        id: turnId,
        parentId: sessionId,
        depth: 1,
        kind: st.role === "actor" ? "actor" : "caller",
        name:
          st.role === "actor"
            ? `${st.actorName || "Third party"} speaks`
            : "Caller turn",
        start,
        end,
        status: "ok",
        output: st.text,
        evals: [],
      });
      return;
    }
    if (!isAgent) return;

    const label = conversational
      ? `Agent turn ${agentIdx.indexOf(i) + 1}`
      : `Step ${agentIdx.indexOf(i) + 1} · ${st.action || st.tool || st.kind || (st.cmd ? "shell" : "step")}`;
    spans.push({
      id: turnId,
      parentId: sessionId,
      depth: 1,
      kind: "turn",
      name: label,
      start,
      end,
      status: offendingTurn ? "error" : "ok",
      output: st.text || st.thought || st.note,
      evals: [],
    });

    const calls = conversational ? callsAt.get(i) || [] : [];
    const stepTool =
      !conversational && (st.action || st.tool || st.cmd || st.kind);
    const guardHere =
      rule &&
      (offendingTurn ||
        (!failedRun && i === agentIdx[Math.min(1, agentIdx.length - 1)]));
    let cursor = start + d * 0.04;

    /* Plan — the model decides what to do (or, with no tools, what to say). */
    const planEnd =
      calls.length || stepTool || guardHere ? start + d * 0.38 : end;
    const planId = `${turnId}::llm0`;
    spans.push({
      id: planId,
      parentId: turnId,
      depth: 2,
      kind: "llm",
      name: calls.length || stepTool ? "LLM · plan" : "LLM · respond",
      start: cursor,
      end: planEnd,
      status: "ok",
      model,
      input: conversational ? steps[i - 1]?.text : st.thought || st.note,
      output:
        calls.length || stepTool
          ? `→ ${calls.map((c) => c.name).join(", ") || stepTool}`
          : st.text,
      evals: [],
    });
    llmShare.push([planId, planEnd - cursor]);
    cursor = planEnd;

    /* The rule check, where one applies. */
    if (guardHere) {
      const gEnd = cursor + Math.min(160, d * 0.08);
      spans.push({
        id: `${turnId}::guard`,
        parentId: turnId,
        depth: 2,
        kind: "guard",
        name: "Guard · rule check",
        start: cursor,
        end: gEnd,
        status: offendingTurn ? "error" : "ok",
        guard: {
          rule,
          verdict: offendingTurn
            ? task.status === "flaky"
              ? "unsure"
              : "breached"
            : "held",
        },
        evals: [],
      });
      cursor = gEnd;
    }

    /* Tool calls between the plan and the reply. */
    calls.forEach((c, k) => {
      const ms = Math.max(30, c.ms || 120);
      const f = c.fault || st.fault || null;
      const status =
        f?.kind === "timeout" || f?.kind === "error" || c.status === "error"
          ? "error"
          : "ok";
      spans.push({
        id: `${turnId}::tool${k}`,
        parentId: turnId,
        depth: 2,
        kind: "tool",
        name: c.name,
        start: cursor,
        end: cursor + ms,
        status,
        tool: {
          name: c.name,
          args: argsText(c.args),
          response:
            c.response ||
            (status === "error"
              ? f?.message || "no response"
              : `${c.rows || 1} ${c.rows === 1 ? "row" : "rows"}${c.wrote ? " written" : ""}`),
          latencyMs: ms,
          status: status === "error" ? f?.kind || "error" : "ok",
          fidelity: fidelityOf(c.name, env, envState),
          fault: f,
          wrote: !!c.wrote,
        },
        evals: [],
      });
      cursor += ms;
    });

    /* A non-conversational step is itself a tool call. */
    if (stepTool) {
      const ms = Math.max(40, Math.round(st.ms || d * 0.35));
      const f = st.fault || null;
      const name = browserTool
        ? `${browserTool}.${st.action || "act"}`
        : st.tool || st.action || (st.cmd ? "shell" : st.kind);
      const fidelityName = browserTool || name;
      const status = f ? "error" : "ok";
      spans.push({
        id: `${turnId}::tool`,
        parentId: turnId,
        depth: 2,
        kind: "tool",
        name,
        start: cursor,
        end: Math.min(end - 40, cursor + ms),
        status,
        tool: {
          name,
          args: st.args
            ? argsText(st.args)
            : st.cmd ||
              [st.target, st.value && `= "${st.value}"`]
                .filter(Boolean)
                .join(" ") ||
              st.subject ||
              "{}",
          response:
            st.result ||
            st.out ||
            st.obs ||
            (st.subject ? st.note : null) ||
            "ok",
          latencyMs: ms,
          status: f ? f.kind || "error" : "ok",
          fidelity: fidelityOf(fidelityName, env, envState),
          fault: f,
          wrote: false,
        },
        evals: [],
      });
      cursor = Math.min(end - 40, cursor + ms);
    }

    /* A tool the scenario needed and the agent never called — marked where
       the agent closed without it. */
    if (offendingTurn && (log.missing || []).length) {
      log.missing.forEach((name, k) => {
        spans.push({
          id: `${turnId}::missing${k}`,
          parentId: turnId,
          depth: 2,
          kind: "missing",
          name: `${name} — never called`,
          start: cursor,
          end: cursor,
          status: "error",
          tool: {
            name,
            args: "—",
            response: "—",
            latencyMs: 0,
            status: "not called",
            fidelity: fidelityOf(name, env, envState),
            fault: null,
          },
          evals: [],
        });
      });
    }

    /* Reply — the model turns the results into what the caller hears. */
    if (planEnd < end && (calls.length || stepTool || guardHere)) {
      const replyId = `${turnId}::llm1`;
      const rs = Math.min(end - 60, cursor + 20);
      spans.push({
        id: replyId,
        parentId: turnId,
        depth: 2,
        kind: "llm",
        name: "LLM · respond",
        start: rs,
        end,
        status: offendingTurn ? "error" : "ok",
        model,
        input: calls.length
          ? `${calls.length} tool result${calls.length === 1 ? "" : "s"}`
          : st.thought || null,
        output: st.text || st.note || null,
        note:
          offendingTurn && log.unsupportedClaim ? log.unsupportedClaim : null,
        evals: [],
      });
      llmShare.push([replyId, end - rs]);
    } else if (offendingTurn) {
      const only = spans.find((s) => s.id === planId);
      only.status = "error";
      if (log.unsupportedClaim) only.note = log.unsupportedClaim;
    }
  });

  /* A voice call's timeline runs at the call's real length (the one billed),
     each step keeping its share of it. */
  if (stage === "voice" && task.durationMs > t && t > 0) {
    const k = task.durationMs / t;
    spans.forEach((sp) => {
      sp.start = Math.round(sp.start * k);
      sp.end = Math.round(sp.end * k);
    });
    t = task.durationMs;
  }

  /* The session wraps everything. */
  const total = Math.max(1, t);
  spans.unshift({
    id: sessionId,
    parentId: null,
    depth: 0,
    kind: "session",
    name: conversational
      ? stage === "voice"
        ? "Call"
        : "Conversation"
      : "Session",
    start: 0,
    end: total,
    status: unmeasured ? "error" : failedRun ? "error" : "ok",
    note: unmeasured
      ? fault.environment ||
        fault.transport ||
        fault.simulator ||
        fault.grading ||
        "No verdict"
      : null,
    evals: [],
  });

  /* Tokens and cost, spread by time over the LLM calls. */
  const llmTotal = llmShare.reduce((a, [, w]) => a + w, 0) || 1;
  const tokens = task.tokens || 0;
  const cost = task.cost || 0;
  llmShare.forEach(([id, w], k) => {
    const s = spans.find((x) => x.id === id);
    const jitter = 0.85 + (hashSeed(`${id}:tok`) % 30) / 100;
    s.tokens = Math.max(40, Math.round((tokens * w * jitter) / llmTotal));
    s.cost = round((cost * w) / llmTotal, 4);
    s.inputTokens = Math.round(s.tokens * (0.7 + (k % 3) * 0.05));
  });

  /* The offending span: the guard that broke, else the reply that went wrong. */
  const offendTurnId = failAt != null ? `${task.id}::t${failAt}` : null;
  const offending = offendTurnId
    ? spans.find(
        (s) =>
          s.parentId === offendTurnId &&
          s.kind === "guard" &&
          s.status === "error",
      ) ||
      spans.find(
        (s) =>
          s.parentId === offendTurnId &&
          s.kind === "llm" &&
          s.status === "error",
      ) ||
      spans.find((s) => s.id === offendTurnId)
    : null;
  if (offending) offending.offending = true;

  /* Eval scores on the span they grade, where that can be told. */
  const guardSpan = spans.find((s) => s.kind === "guard");
  const closing =
    lastAgent != null
      ? spans
          .filter(
            (s) =>
              s.parentId === `${task.id}::t${lastAgent}` && s.kind === "llm",
          )
          .slice(-1)[0]
      : null;
  (task.evalResults || []).forEach((r) => {
    let target = null;
    if (r.id === "policy_adherence")
      target = guardSpan || (!r.passed ? offending : null);
    else if (r.id === "task_success")
      target = !r.passed && offending ? offending : closing;
    else if (r.id === "pii_leakage") target = !r.passed ? offending : null;
    else if (!r.passed) target = offending;
    (target || spans[0]).evals.push({
      id: r.id,
      name: r.name,
      score: r.score,
      passed: r.passed,
      reason: r.reason,
    });
  });

  return {
    spans,
    total,
    offendingId: offending?.id || null,
    sessionId,
    model,
    unmeasured,
  };
};

/** Children by parent, for drawing the tree in order. */
export const spanTree = (spans) => {
  const kids = new Map();
  spans.forEach((s) => {
    kids.set(s.parentId, [...(kids.get(s.parentId) || []), s]);
  });
  const out = [];
  const walk = (pid) =>
    (kids.get(pid) || []).forEach((s) => {
      out.push({ ...s, hasChildren: kids.has(s.id) });
      walk(s.id);
    });
  walk(null);
  return out;
};

/* "760ms", "8.6s", "15s", and a minute or more as "2m 33s". */
export const fmtMs = (ms) => {
  if (ms >= 60000) {
    const sec = Math.round(ms / 1000);
    return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  }
  return ms >= 1000
    ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s`
    : `${Math.round(ms)}ms`;
};
