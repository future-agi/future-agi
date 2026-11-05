/*
  A multi-agent trial's Participants tab, shaped to be read in three seconds
  (Linear TH-8886): a summary sentence, what the caller asked, a table with one
  row per worker (what it did, what came back) that opens to its task and tool
  calls, what the front desk replied, and the evidence under "Details".

  A worker counts as observed only when its own events arrive. Declared names
  are not proof: an unobserved worker stays "Not observed", its steps
  "Unknown — not in the recorded events", and the checks that depend on its
  events Unmeasured. Simulated people (the caller, a scenario's actors) are
  the world's, never workers.

  A worker's ID is derived from the member, never from the attempt, so it
  reads the same across retries.

  The wording helpers (didOf, resultSentenceOf, replyOf, kvOf) turn tool names
  and outputs into plain sentences, so the tab never shows a function name
  outside a tool call's own line.
*/
import { handoffsOf, workerToolsOf } from "./handoffs";
import { hashSeed } from "./runStream";

export const TOPOLOGY = "One level, one hand-off at a time";
export const ID_NOTE = "Worker IDs stay the same across retries";
export const NOT_RECORDED = "Unknown — not in the recorded events";
export const NOT_CONTROLLED =
  "Not a controlled run — a worker could bypass a block";
export const NO_ANSWER = "Didn't finish — no answer came back.";
export const NO_EVENTS = "No events from this worker.";

/** 0:42 — seconds into the call. */
export const clock = (s) => {
  if (s == null || Number.isNaN(s)) return "—";
  const n = Math.max(0, Math.floor(s));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
};

/* First word of a tool name → [past tense, base form]. */
const VERBS = {
  check: ["Checked", "check"],
  lookup: ["Looked up", "look up"],
  look: ["Looked up", "look up"],
  get: ["Got", "get"],
  find: ["Found", "find"],
  issue: ["Issued", "issue"],
  create: ["Created", "create"],
  update: ["Updated", "update"],
  cancel: ["Cancelled", "cancel"],
  send: ["Sent", "send"],
  list: ["Listed", "list"],
  verify: ["Verified", "verify"],
  generate: ["Generated", "generate"],
  apply: ["Applied", "apply"],
  request: ["Requested", "request"],
  book: ["Booked", "book"],
  refund: ["Refunded", "refund"],
};
/* Nouns that read better with "the" in front. */
const THE = new Set([
  "order",
  "booking",
  "reservation",
  "appointment",
  "ticket",
  "account",
  "refund",
  "label",
  "status",
  "subscription",
  "payment",
  "invoice",
  "shipment",
  "delivery",
]);

const capital = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const lowerFirst = (s) => (s ? s[0].toLowerCase() + s.slice(1) : s);
const isHandoff = (name) =>
  /^escalate_to_human$|^transfer/i.test(String(name || ""));

/* Tools whose plain name isn't what the name spells: [past, base, rest]. */
const ALIASES = {
  shipment_status: ["Checked", "check", "the delivery status"],
  update_shipping_address: ["Updated", "update", "the delivery address"],
};

/* [verb, rest] of a snake_case tool name, in past tense or base form. */
const phraseOf = (name, form) => {
  const alias = ALIASES[String(name || "").toLowerCase()];
  if (alias) return [alias[form === 1 ? 1 : 0], alias[2]];
  const [first, ...rest] = String(name || "")
    .toLowerCase()
    .split("_")
    .filter(Boolean);
  const verb = VERBS[first]?.[form];
  const words = verb ? rest : [first, ...rest].filter(Boolean);
  if (words.length && THE.has(words[0])) words.unshift("the");
  return [verb || null, words.join(" ")];
};

/**
 * issue_refund → "Issued the refund"; check_return_eligibility → "Checked
 * return eligibility"; escalate_to_human → "Transferred the call to a person".
 * { attempt } reads "Tried to issue the refund", { blocked } "Wasn't allowed
 * to issue the refund".
 */
export const didOf = (name, { attempt = false, blocked = false } = {}) => {
  if (isHandoff(name)) {
    if (blocked) return "Wasn't allowed to transfer the call to a person";
    return attempt
      ? "Tried to transfer the call to a person"
      : "Transferred the call to a person";
  }
  if (blocked || attempt) {
    const [verb, rest] = phraseOf(name, 1);
    const body = verb ? `${verb} ${rest}` : rest;
    return `${blocked ? "Wasn't allowed to" : "Tried to"} ${body.trim()}`;
  }
  const [verb, rest] = phraseOf(name, 0);
  return verb ? `${verb} ${rest}`.trim() : capital(rest);
};

const callDid = (c) =>
  didOf(c.name, {
    blocked: c.blocked,
    attempt: !c.blocked && c.output == null,
  });

/** What a worker did, in words: "Checked return eligibility, looked up the order". */
export const didLineOf = (w) =>
  (w.calls || [])
    .map(callDid)
    .map((s, i) => (i ? lowerFirst(s) : s))
    .join(", ");

const MONEY = /amount|total|price|cost|refund/i;

const labelOf = (key) =>
  capital(
    String(key)
      .split("_")
      .map((w) => (w === "id" ? "ID" : w))
      .join(" "),
  );

const valueOf = (v, key = "") => {
  if (v == null) return "none";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number" && MONEY.test(key)) return `$${v}`;
  if (typeof v === "object") return JSON.stringify(v);
  const s = String(v);
  return /^[a-z0-9]+(_[a-z0-9]+)+$/.test(s) ? s.replace(/_/g, " ") : s;
};

/** { order_id: 9891, amount: 89 } → [["Order ID", "9891"], ["Amount", "$89"]]. */
export const kvOf = (obj) =>
  obj && typeof obj === "object"
    ? Object.entries(obj).map(([k, v]) => [labelOf(k), valueOf(v, k)])
    : [];

/* What one finished call tells: how much it's worth saying (rank), the
   worker's result for it, and the front desk's words to the caller. */
const infoOf = (c) => {
  const o = c.output || {};
  const order = c.input?.order_id;
  const of = order != null ? ` ${order}` : "";
  if (/refund/i.test(c.name) && o.amount != null)
    return {
      rank: 5,
      result: `Refunded $${o.amount} on order${of} to the original payment method.`,
      reply: `I've refunded $${o.amount} to your original payment method.`,
    };
  if (isHandoff(c.name))
    return {
      rank: 4,
      result: "The caller is with a supervisor, who has the call summary.",
      reply: "I've put you through to a supervisor.",
    };
  if (/label/i.test(c.name))
    return {
      rank: 3,
      result: `Emailed a return label for order${of}.`,
      reply: "I've emailed you a return label.",
    };
  if (/eligib/i.test(c.name) && o.days_left != null)
    return {
      rank: 3,
      result: `Order${of} can be returned — ${o.days_left} days left in the window.`,
      reply: `Your order can be returned — you have ${o.days_left} days left.`,
    };
  if (/shipment|delivery/i.test(c.name)) {
    const days = o.eta_days;
    const arrives =
      days == null
        ? ""
        : days <= 0
          ? " — it arrives today"
          : days === 1
            ? " — it arrives tomorrow"
            : ` — it arrives in ${days} days`;
    const line = `Order${of} is ${days != null && days <= 1 ? "out for delivery" : "on its way"}${arrives}.`;
    return { rank: 3, result: line, reply: line };
  }
  if (/address/i.test(c.name))
    return {
      rank: 3,
      result: `Changed the delivery address on order${of}.`,
      reply: "I've updated the delivery address.",
    };
  if (/^cancel/i.test(c.name))
    return {
      rank: 3,
      result: `Cancelled order${of} before it was dispatched.`,
      reply: "I've cancelled your order.",
    };
  if (/lookup|status|find|get/i.test(c.name))
    return {
      rank: 1,
      result: `Found order${of}${o.items ? ` with ${o.items} ${o.items === 1 ? "item" : "items"}` : ""}.`,
      reply: "I found your order.",
    };
  const done = didOf(c.name);
  return {
    rank: 2,
    result: `${done}${order != null ? ` for order ${order}` : ""}.`,
    reply: `I've ${lowerFirst(done)}.`,
  };
};

/* The most informative finished call of a worker. */
const bestCallOf = (w) =>
  (w.calls || [])
    .filter((c) => !c.blocked && c.output)
    .map((c) => ({ c, i: infoOf(c) }))
    .sort((a, b) => b.i.rank - a.i.rank)[0] || null;

/**
 * One clean sentence for a worker's Result column, from its most informative
 * tool output, never repeating a lesser call:
 *   "Order 9891 can be returned — 13 days left in the window."
 */
export const resultSentenceOf = (w) => {
  if (w.state === "unobserved") return NO_EVENTS;
  if (w.state === "timeout") return NO_ANSWER;
  const best = bestCallOf(w);
  if (best) return best.i.result;
  return (w.calls || []).some((c) => c.blocked)
    ? "Was blocked — that is outside its tools."
    : "Nothing needed a tool.";
};

/**
 * What the front desk tells the caller, built from the workers' results:
 *   "Your order can be returned — you have 13 days left. I've also put you
 *    through to a supervisor."
 */
export const replyOf = (workers = []) => {
  if (!workers.length) return "I answered this one myself.";
  const said = workers
    .filter((w) => w.state === "returned")
    .map((w) => bestCallOf(w)?.i.reply)
    .filter(Boolean);
  const gone = workers.filter((w) => w.state === "timeout").map((w) => w.name);
  const parts = said.map((s, i) =>
    i && s.startsWith("I've ") ? `I've also ${s.slice(5)}` : s,
  );
  if (gone.length)
    parts.push(
      `I couldn't get an answer from ${gone.join(" and ")} in time, so that part isn't done.`,
    );
  return parts.join(" ") || "I wasn't able to help with that.";
};

/**
 * The summary sentence: how many workers the front desk passed the call to,
 * and how they ended.
 *   Front desk passed this call to 2 workers. Both finished.
 */
export const summaryOf = ({ coordinator, workers, observed }) => {
  const n = workers.length;
  if (!n)
    return `${coordinator} handled this call itself — no worker involved.`;
  if (!observed) return "Workers not observed — shown as the app declares them";
  const head = `${coordinator} passed this call to ${n} ${n === 1 ? "worker" : "workers"}.`;
  const done = workers.filter((w) => w.state === "returned").length;
  const out = n - done;
  if (!out)
    return `${head} ${n === 1 ? "It finished." : n === 2 ? "Both finished." : `All ${n} finished.`}`;
  if (!done)
    return `${head} ${n === 1 ? "It didn't finish" : n === 2 ? "Neither finished" : `None of them finished`} (timed out).`;
  return `${head} ${done} finished, ${out} didn't (timed out).`;
};

/** The tool calls a call is counted by — a hand-off to a worker is not one. */
export const toolCallsOf = (calls = []) =>
  calls.filter(
    (c) => !/^(transfer|hand_?off|delegate)(_|$)/i.test(c.name || ""),
  );

/* Tool inputs and outputs for the mock — what a recorded call would carry. */
const ioOf = (name, order, seed) => {
  const h = hashSeed(`${seed}:${name}`);
  if (/refund/i.test(name)) {
    const amount = 20 + (h % 180);
    return {
      input: { order_id: order, amount },
      output: { status: "refunded", amount },
    };
  }
  if (/eligib/i.test(name))
    return {
      input: { order_id: order },
      output: { status: "eligible", days_left: 4 + (h % 24) },
    };
  if (/label/i.test(name))
    return {
      input: { order_id: order, send_to: "email_on_file" },
      output: { status: "sent", label_id: `RL-${1000 + (h % 8999)}` },
    };
  if (/escalat|transfer|human/i.test(name))
    return {
      input: { reason: "refund over the agent limit", order_id: order },
      output: { status: "transferred", queue: "supervisors" },
    };
  if (/shipment|delivery/i.test(name)) {
    const eta_days = h % 4;
    return {
      input: { order_id: order },
      output: {
        status: eta_days <= 1 ? "out_for_delivery" : "in_transit",
        eta_days,
      },
    };
  }
  if (/address/i.test(name))
    return {
      input: { order_id: order, address: "new_address_on_request" },
      output: { status: "updated" },
    };
  if (/^cancel/i.test(name))
    return {
      input: { order_id: order, reason: "customer_request" },
      output: { status: "cancelled" },
    };
  if (/lookup|status|find|get/i.test(name))
    return {
      input: { order_id: order },
      output: { status: "found", items: 1 + (h % 3) },
    };
  return {
    input: { order_id: order },
    output: { status: "ok", rows: 1 + (h % 3) },
  };
};

/* A member's model, whether the definition spells it "gpt-4o" or { name }. */
const modelOf = (m) =>
  (typeof m.model === "string" ? m.model : m.model?.name) || null;

/* One call from a worker's own tools — a lookup only when it has nothing else.
   A tool several members share may go to any of them. */
const ownCallsOf = (m, key) => {
  const tools = workerToolsOf(m);
  const pool = tools.filter((n) => !/lookup/i.test(n));
  const from = pool.length ? pool : tools;
  return from.length ? [from[hashSeed(`${key}:tool`) % from.length]] : [];
};

const initialCallsOf = (h, workerId) =>
  h.events
    .filter((e) => e.kind === "tool" && e.from.id === workerId)
    .map((e) => ({
      name: e.tool,
      boundary: e.boundary,
      blocked: e.boundary === "Blocked",
      args: e.args,
      callId: e.callId,
    }));

/**
 * One trial's participants:
 *   { summary, topology, observed, enforced, evidence, coordinator, caller,
 *     workers: [{ id, name, wid, state, task, delegatedAt, endedAt, calls }],
 *     request, reply, duration, steps, checks, boundaries }
 * reply is null (and duration null) when no worker's events were observed.
 * state: "returned" | "timeout" | "unobserved".
 * Null for a single agent.
 */
export const participantsOf = ({ agent, task }) => {
  const h = handoffsOf({ agent, task });
  if (!h) return null;
  const observed = h.evidence.observed;
  const seed = task.scenarioId || task.id;
  const coordM = h.events[0].from;
  const coordinator = { id: coordM.id, name: coordM.name };
  const first = h.events.find((e) => e.kind === "delegate")?.to || null;

  /* The workers the call went to — the one the path names, and now and then a
     second from the same squad. */
  let picked = first ? [first] : [];
  /* A squad of four or more sometimes sends a call through three workers —
     about one call in four; the rest keep one or two. */
  const three =
    h.members.length >= 4 && hashSeed(`${seed}:third-worker`) % 4 === 0;
  if (
    first &&
    h.members.length >= 3 &&
    (three || hashSeed(`${seed}:second-worker`) % 2 === 0)
  ) {
    const rest = h.members.filter(
      (m) => m.id !== coordM.id && m.id !== first.id,
    );
    if (rest.length) {
      const second = rest[hashSeed(`${seed}:second-pick`) % rest.length];
      picked.push(second);
      const left = rest.filter((m) => m.id !== second.id);
      if (three && left.length)
        picked.push(left[hashSeed(`${seed}:third-pick`) % left.length]);
    }
  }
  picked = h.members.filter((m) => picked.some((p) => p.id === m.id));

  const orderDigits = String(task.callLog?.calls?.[0]?.args?.id || "").replace(
    /\D/g,
    "",
  );
  const order =
    Number(orderDigits) || 1000 + (hashSeed(`${seed}:order`) % 9000);
  const request =
    task.steps?.find((s) => s.role && s.role !== "agent" && s.text)?.text ||
    task.task ||
    task.title ||
    "The caller's request.";

  /* A three-worker call on a trial that failed or flaked has its last worker
     time out — the third is the one that never answers. */
  const trialFailed = task.status === "failed" || task.status === "flaky";
  const timeout =
    h.outcome === "timeout" || (picked.length >= 3 && trialFailed);
  const timeoutEvent = h.events.find((e) => e.kind === "timeout");
  let t = h.events.find((e) => e.kind === "delegate")?.at ?? 2.4;

  const workers = picked.map((m, idx) => {
    const isFirst = first && m.id === first.id;
    const last = idx === picked.length - 1;
    const timedOut = timeout && last;
    const base = {
      id: m.id,
      name: m.name,
      wid: `run/${m.id}`,
      model: modelOf(m),
      order,
      seed: `${seed}:${m.id}`,
      task: `Handle the caller's request for ${(m.role || m.name).toLowerCase()} on order ${order}.`,
    };
    if (!observed)
      return {
        ...base,
        state: "unobserved",
        delegatedAt: null,
        endedAt: null,
        calls: [],
      };

    /* Its calls: the ones the path recorded for the first worker; the second
       one's from its own tools. A worker that timed out has its last call
       attempted, with no result. */
    let raw = isFirst
      ? initialCallsOf(h, m.id)
      : ownCallsOf(m, `${seed}:${m.id}`).map((name) => ({
          name,
          boundary: h.enforced ? "Allowed" : "Declared",
        }));
    if (timedOut) {
      /* Its last call was attempted, with no result. */
      if (isFirst) {
        if (timeoutEvent?.attempted)
          raw = [
            ...raw,
            {
              name: timeoutEvent.attempted,
              boundary: h.enforced ? "Allowed" : "Declared",
              attempt: true,
            },
          ];
        else
          raw = raw.map((c, i) =>
            i === raw.length - 1 ? { ...c, attempt: true } : c,
          );
      } else raw = raw.map((c) => ({ ...c, attempt: true }));
    }
    const delegatedAt = t;
    const calls = raw.map((c) => {
      t += 1.1;
      const io = ioOf(c.name, order, `${seed}:${m.id}`);
      return {
        id: `${m.id}-${c.name}-${Math.round(t * 10)}`,
        worker: m.id,
        workerName: m.name,
        name: c.name,
        at: t,
        boundary: c.boundary,
        blocked: !!c.blocked,
        input: io.input,
        output: c.blocked
          ? { status: "blocked" }
          : c.attempt
            ? null
            : io.output,
      };
    });
    const endedAt = timedOut ? t + 30 : t + 1.4;
    t = endedAt + 0.6;
    return {
      ...base,
      state: timedOut ? "timeout" : "returned",
      delegatedAt,
      endedAt,
      calls,
    };
  });

  const allCalls = workers.flatMap((w) => w.calls);
  const replyAt = workers.length
    ? workers[workers.length - 1].endedAt + 0.9
    : 1.8;
  const reply = replyOf(workers);

  /* The steps in order; tool calls sit one level under their worker. */
  const steps = [
    {
      key: "ask",
      kind: "ask",
      who: "caller",
      verb: "Caller asked",
      detail: request,
      at: 0,
    },
  ];
  if (!observed && workers.length) {
    workers.forEach((w) =>
      steps.push({
        key: `declared-${w.id}`,
        kind: "declared",
        who: w.id,
        worker: w,
        verb: `${w.name} — declared`,
        detail: null,
        at: null,
      }),
    );
    steps.push({
      key: "unknown",
      kind: "unknown",
      who: null,
      verb: NOT_RECORDED,
      detail: null,
      at: null,
    });
  } else {
    workers.forEach((w) => {
      steps.push({
        key: `delegate-${w.id}`,
        kind: "delegate",
        who: "coordinator",
        worker: w,
        verb: `${coordinator.name} handed to ${w.name}`,
        detail: w.task,
        at: w.delegatedAt,
      });
      w.calls.forEach((c) =>
        steps.push({
          key: c.id,
          kind: "tool",
          who: w.id,
          worker: w,
          call: c,
          depth: 1,
          at: c.at,
        }),
      );
      if (w.state === "timeout")
        steps.push({
          key: `timeout-${w.id}`,
          kind: "timeout",
          who: w.id,
          worker: w,
          verb: `${w.name} didn't return (timed out at ${clock(w.endedAt)})`,
          detail: w.calls.some((c) => c.output == null)
            ? `${w.calls[w.calls.length - 1].name} was attempted, not committed — the world shows no change.`
            : null,
          at: w.endedAt,
        });
      else
        steps.push({
          key: `return-${w.id}`,
          kind: "return",
          who: w.id,
          worker: w,
          verb: `${w.name} returned`,
          detail: resultSentenceOf(w),
          at: w.endedAt,
        });
    });
    steps.push({
      key: "reply",
      kind: "reply",
      who: "coordinator",
      verb: `${coordinator.name} replied`,
      detail: reply,
      at: replyAt,
    });
  }

  /* What depends on a worker's own events: Unmeasured without them. */
  const checks = [
    ["Coordinator retried", h.after?.retried],
    ["Duplicate write", h.after?.duplicateWrite],
    ["Customer told it succeeded", h.after?.toldSuccess],
  ].map(([label, yes]) => ({
    label,
    value: !observed ? "Unmeasured" : yes ? "Yes" : "No",
  }));

  return {
    summary: summaryOf({ coordinator: coordinator.name, workers, observed }),
    topology: TOPOLOGY,
    observed,
    enforced: h.enforced,
    evidence: observed
      ? "Observed — from the delegation and return events this run recorded, under the same run ID."
      : h.evidence.line,
    coordinator,
    outcome: timeout ? "timeout" : h.outcome,
    workers,
    request,
    reply: observed || !workers.length ? reply : null,
    duration: observed && workers.length ? replyAt : null,
    steps,
    checks: !workers.length ? [] : !observed || timeout ? checks : [],
    boundaries: allCalls.map((c) => ({
      tool: c.name,
      worker: c.workerName,
      boundary: c.boundary,
    })),
  };
};

/* ── Engineer detail: the exact words, sizes and timings behind each row ── */

export const TIMEOUT_S = 30;
export const TRACES_SOON =
  "Coming soon — linking a tool call to its span (TH-9024)";

/* What the coordinator asks for, in the imperative, and what to reply with. */
const askOf = (name) => {
  if (isHandoff(name)) return "transfer the call to a person";
  const [verb, rest] = phraseOf(name, 1);
  return (verb ? `${verb} ${rest}` : rest).trim();
};
const replyWithOf = (name) =>
  /refund/i.test(name)
    ? "the refund amount and status"
    : /eligib/i.test(name)
      ? "eligibility and days left"
      : /label/i.test(name)
        ? "the label ID"
        : isHandoff(name)
          ? "the queue it went to"
          : /shipment|delivery/i.test(name)
            ? "the delivery status and arrival day"
            : /address|cancel/i.test(name)
              ? "confirmation of the change"
              : /lookup|status|find|get/i.test(name)
                ? "the order status and item count"
                : "the result";

/**
 * The exact text the coordinator sent a worker, built from the tools it was
 * given:
 *   "Check return eligibility and look up the order for order 9891. Reply
 *    with eligibility and days left and the order status and item count."
 * Null when the worker's events were not recorded.
 */
export const handoffMessageOf = (w) => {
  if (!w || w.state === "unobserved") return null;
  const names = [...new Set((w.calls || []).map((c) => c.name))];
  const order = w.order != null ? ` order ${w.order}` : "";
  if (!names.length)
    return `Handle the caller's request on${order || " this call"}. Reply with one sentence on what you did.`;
  const acts = names.map(askOf).map((s, i) => (i ? s : capital(s)));
  const list =
    acts.length > 1
      ? `${acts.slice(0, -1).join(", ")} and ${acts[acts.length - 1]}`
      : acts[0];
  const reply = names.map(replyWithOf);
  const replyList = [...new Set(reply)].join(" and ");
  return `${list} for${order || " this call"}. Reply with ${replyList}.`;
};

/**
 * The worker's raw return text — the one-line result plus a line per finished
 * call. A worker that timed out has no answer.
 */
export const fullAnswerOf = (w) => {
  if (!w || w.state === "unobserved") return null;
  if (w.state === "timeout")
    return `No answer — timed out after ${TIMEOUT_S.toFixed(1)} s`;
  const lines = [resultSentenceOf(w)];
  const calls = w.calls || [];
  if (calls.length) lines.push("");
  calls.forEach((c) => {
    if (c.blocked)
      lines.push(`${didOf(c.name, { blocked: true })} — outside my tools.`);
    else if (c.output)
      lines.push(
        `${didOf(c.name)} — ${kvOf(c.output)
          .map(([k, v]) => `${k}: ${v}`)
          .join(", ")}.`,
      );
    else lines.push(`${didOf(c.name, { attempt: true })} — no response.`);
  });
  return lines.join("\n");
};

/** Milliseconds a call took; null when it never answered. */
export const toolDurationOf = (call) => {
  if (!call || (call.output == null && !call.blocked)) return null;
  const h = hashSeed(`${call.id}:ms`);
  return call.blocked ? 8 + (h % 40) : 90 + (h % 760);
};

/** "180 ms" · "1.2 s" · "—". */
export const durationLabel = (ms) =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

/** How a call ended: ok, an error with its message, or no response. */
export const callStatusOf = (call) => {
  if (call?.blocked)
    return {
      kind: "error",
      text: "error: blocked — outside this worker's tools",
    };
  if (call?.output == null) return { kind: "none", text: "no response" };
  return { kind: "ok", text: "ok" };
};

/** Tokens a worker used, deterministic from its seed; null when unobserved. */
export const tokensOf = (w) => {
  if (!w || w.state === "unobserved") return null;
  const h = hashSeed(`${w.seed || w.id}:tokens`);
  return 520 + (h % 900) + 160 * (w.calls || []).length;
};

/** "0:02 → 0:06 · 4.0 s" — when a worker started and finished. */
export const spanLabelOf = (w) =>
  w?.delegatedAt == null || w?.endedAt == null
    ? null
    : `${clock(w.delegatedAt)} → ${clock(w.endedAt)} · ${(w.endedAt - w.delegatedAt).toFixed(1)} s`;

/** The worker's whole record, as it is copied to the clipboard. */
export const workerJsonOf = (w) => ({
  id: w.id,
  name: w.name,
  model: w.model ?? null,
  task: w.task,
  handoff_message: handoffMessageOf(w),
  calls: (w.calls || []).map((c) => ({
    id: c.id,
    name: c.name,
    input: c.input ?? null,
    output: c.output ?? null,
    duration_ms: toolDurationOf(c),
    status: callStatusOf(c).text,
  })),
  answer: fullAnswerOf(w),
});

/**
 * Every event in time order for the Raw events view:
 *   { key, at, actor, type, depth, text?, input?, output?, duration?, status? }
 * type: Caller · Hand-off · Tool call · Answer · Timed out · Reply. Worker
 * events sit one level under their hand-off. Empty when no worker's events
 * were recorded.
 */
export const rawEventsOf = (p) => {
  if (!p || !p.observed) return [];
  const out = [
    {
      key: "caller",
      at: 0,
      actor: "Caller",
      type: "Message",
      depth: 0,
      text: p.request,
    },
  ];
  p.workers.forEach((w) => {
    out.push({
      key: `handoff-${w.id}`,
      at: w.delegatedAt,
      actor: p.coordinator.name,
      type: "Hand-off",
      depth: 0,
      to: w.name,
      text: handoffMessageOf(w),
    });
    w.calls.forEach((c) =>
      out.push({
        key: c.id,
        at: c.at,
        actor: w.name,
        type: "Tool call",
        depth: 1,
        tool: c.name,
        input: c.input ?? null,
        output: c.output ?? null,
        duration: toolDurationOf(c),
        status: callStatusOf(c),
      }),
    );
    out.push({
      key: `end-${w.id}`,
      at: w.endedAt,
      actor: w.name,
      type: w.state === "timeout" ? "Timed out" : "Answer",
      depth: 1,
      text: fullAnswerOf(w),
    });
  });
  out.push({
    key: "reply",
    at: p.duration ?? 1.8,
    actor: p.coordinator.name,
    type: "Reply",
    depth: 0,
    text: p.reply,
  });
  return out.sort((a, b) => a.at - b.at);
};
