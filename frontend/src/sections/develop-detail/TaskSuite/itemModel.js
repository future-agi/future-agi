// Imported items (R2-SPEC P5 §7.3.6–§7.3.7): how a conversation, a task or
// a trace row reads in the grid and the Row view, and the replay preview
// line. Pure; never throws. Shapes: the `conversation` data type
// `{turns:[{n, role, content, tool_calls?, attachments?, evals?}],
// resolution, resolution_reason, failing_turn, channel}` and the row
// `source {kind, project_id, trace_id, span_id, session_id, captured_at,
// frozen, expired?}` (r2Shapes.js).

/** JSON text → value; objects pass through; anything else → null. */
export function parseJson(value) {
  if (value == null || value === "") return null;
  if (typeof value === "object") return value;
  if (typeof value !== "string") return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

const ROLE = {
  customer: "customer",
  user: "customer",
  human: "customer",
  caller: "customer",
  agent: "agent",
  assistant: "agent",
  model: "agent",
  ai: "agent",
  bot: "agent",
  system: "system",
  developer: "system",
  tool: "tool",
};

const textOf = (content) => {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content
      .map((p) => (typeof p === "string" ? p : p?.text ?? ""))
      .filter(Boolean)
      .join("\n");
  if (typeof content === "object" && typeof content.text === "string")
    return content.text;
  return JSON.stringify(content);
};

/**
 * A conversation value (JSON text, `{turns}`, `{messages}` or a plain
 * `[{role, content}]` array) → `{turns, systemPrompt, resolution,
 * resolutionReason, failingTurn, channel, audioUrl, transcript}` or null.
 * Turns are renumbered 1…n without system messages; roles read `customer`
 * / `agent` / `tool`.
 */
export function parseConversation(value) {
  const raw = parseJson(value);
  if (!raw) return null;
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(raw.turns)
      ? raw.turns
      : Array.isArray(raw.messages)
        ? raw.messages
        : null;
  if (!list) return null;
  const system = [];
  const turns = [];
  list.forEach((t) => {
    if (!t || typeof t !== "object") return;
    const role = ROLE[String(t.role ?? "").toLowerCase()] ?? "customer";
    if (role === "system") {
      system.push(textOf(t.content));
      return;
    }
    turns.push({
      n: turns.length + 1,
      sourceN: Number.isFinite(t.n) ? t.n : turns.length + 1,
      role,
      // IT-R3 G5 P2: imported turns carry `text` (`{n, role, text,
      // tool_calls}`), chat exports `content` or `message`.
      content: textOf(t.content ?? t.text ?? t.message),
      toolCalls: Array.isArray(t.tool_calls) ? t.tool_calls : [],
      attachments: Array.isArray(t.attachments) ? t.attachments : [],
      helpfulness: verdictOf(t.evals?.helpfulness),
      at: t.at ?? t.timestamp ?? t.created_at ?? null,
    });
  });
  const obj = Array.isArray(raw) ? {} : raw;
  const systemPrompt =
    typeof obj.system_prompt === "string" && obj.system_prompt
      ? obj.system_prompt
      : system.join("\n\n");
  // `failing_turn` is numbered like the source turns.
  const failing = Number(obj.failing_turn);
  const mapped = Number.isFinite(failing)
    ? turns.find((t) => t.sourceN === failing)?.n ?? null
    : null;
  return {
    turns,
    systemPrompt: systemPrompt || "",
    resolution: verdictOf(obj.resolution),
    resolutionReason:
      typeof obj.resolution_reason === "string" ? obj.resolution_reason : "",
    failingTurn: mapped ?? firstFailingAgentTurn(turns),
    channel: obj.channel ?? null,
    audioUrl: obj.audio_url ?? null,
    transcript: Array.isArray(obj.transcript) ? obj.transcript : null,
  };
}

/** `Pass` / `Fail` / null from 1, 0, true, "passed", "Fail", … */
export function verdictOf(v) {
  if (v === 1 || v === true) return "Pass";
  if (v === 0 || v === false) return "Fail";
  const s = String(v ?? "").toLowerCase();
  if (["pass", "passed", "ok", "yes"].includes(s)) return "Pass";
  if (["fail", "failed", "no"].includes(s)) return "Fail";
  return null;
}

export const firstFailingAgentTurn = (turns) =>
  (turns ?? []).find((t) => t.role === "agent" && t.helpfulness === "Fail")
    ?.n ?? null;

const clip = (s, n) => {
  const text = String(s ?? "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > n ? `${text.slice(0, n - 1).trimEnd()}…` : text;
};

const plural = (n, one, many = `${one}s`) =>
  `${Number(n).toLocaleString("en-US")} ${n === 1 ? one : many}`;

/** `6 turns · 3 tool calls · ends: "I'll file a chargeback"`. */
export function conversationSummary(conv, { clipChars = 40 } = {}) {
  if (!conv?.turns?.length) return "No turns";
  const calls = conv.turns.reduce((n, t) => n + t.toolCalls.length, 0);
  const last = [...conv.turns].reverse().find((t) => t.content);
  const parts = [plural(conv.turns.length, "turn")];
  if (calls) parts.push(plural(calls, "tool call"));
  if (last) parts.push(`ends: "${clip(last.content, clipChars)}"`);
  return parts.join(" · ");
}

const ROLE_LABEL = { customer: "Customer", agent: "Agent", tool: "Tool" };
export const roleLabel = (role) => ROLE_LABEL[role] ?? "Customer";

/**
 * One dot per turn: `pass` (green), `fail` (red), `none` (grey), with the
 * tooltip `Turn 4 · Agent · Helpfulness Fail`.
 */
export function turnDots(conv) {
  return (conv?.turns ?? []).map((t) => ({
    n: t.n,
    role: t.role,
    verdict:
      t.helpfulness === "Pass"
        ? "pass"
        : t.helpfulness === "Fail"
          ? "fail"
          : "none",
    tooltip: `Turn ${t.n} · ${roleLabel(t.role)} · ${
      t.helpfulness ? `Helpfulness ${t.helpfulness}` : "Not scored"
    }`,
  }));
}

/** `●●○●●○` (screen readers and copy). */
export const dotText = (dots) =>
  (dots ?? []).map((d) => (d.verdict === "none" ? "○" : "●")).join("");

/**
 * The turn strip (F9): `[{n, label, mark}]`, `mark` ✓ / ✗ on scored agent
 * turns; label `4 Agent`.
 */
export function turnStrip(conv) {
  return (conv?.turns ?? []).map((t) => ({
    n: t.n,
    role: t.role,
    label: `${t.n} ${roleLabel(t.role)}`,
    mark: t.helpfulness === "Pass" ? "✓" : t.helpfulness === "Fail" ? "✗" : "",
    failing: t.n === conv.failingTurn,
  }));
}

/**
 * `Conversation: Fail · Resolution · "<reason>" · First failing turn: 4`
 * (parts without data are left out; null without a resolution).
 */
export function verdictLine(conv) {
  if (!conv?.resolution) return null;
  const parts = [`Conversation: ${conv.resolution}`, "Resolution"];
  if (conv.resolutionReason) parts.push(`"${conv.resolutionReason}"`);
  if (conv.failingTurn) parts.push(`First failing turn: ${conv.failingTurn}`);
  return parts.join(" · ");
}

// ---------------------------------------------------------------- tools

const argText = (v) => {
  if (typeof v === "string") return `"${clip(v, 28)}"`;
  if (v == null) return "null";
  if (typeof v === "object") return clip(JSON.stringify(v), 28);
  return String(v);
};

/** `{code, tone, label}` of a recorded tool result: `404` red, `200` green. */
export function toolOutcome(call) {
  const result = parseJson(call?.result) ?? call?.result;
  const err = result && typeof result === "object" ? result.error : null;
  const code =
    Number(err?.code) ||
    Number(call?.status_code) ||
    (call?.status === "error" || err ? null : 200);
  const failed = call?.status === "error" || Boolean(err) || code >= 400;
  return {
    code: code || null,
    tone: failed ? "error" : "ok",
    label: code ? String(code) : failed ? "error" : "ok",
    message: typeof err?.message === "string" ? err.message : null,
  };
}

/** `lookup_order(order_id="8830") → 404`. */
export function toolCallText(call) {
  const args = call?.args ?? call?.arguments ?? {};
  const parsed = parseJson(args) ?? args;
  const argList =
    parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? Object.entries(parsed)
          .map(([k, v]) => `${k}=${argText(v)}`)
          .join(", ")
      : "";
  return `${call?.name ?? "tool"}(${argList}) → ${toolOutcome(call).label}`;
}

// --------------------------------------------------------------- replay

export const REPLAY_MODES = Object.freeze([
  {
    value: "failing_turn",
    label: "Regenerate the failing turn (history frozen)",
  },
  {
    value: "rollout",
    label: "Roll out from that turn (simulated customer continues)",
  },
  { value: "full", label: "Full re-simulation from the customer's goal" },
  {
    value: "score_only",
    label: "Score the recorded conversation only (no new calls)",
  },
]);

export const REPLAY_TOOLS = Object.freeze([
  { value: "recorded", label: "Recorded" },
  { value: "mock", label: "Mock" },
  { value: "live", label: "Live sandbox" },
]);

export const DEFAULT_REPLAY = Object.freeze({
  mode: "failing_turn",
  tools: "recorded",
});

const uniq = (xs) => [...new Set(xs)];

/** Recorded tool calls of the whole conversation (names in order, unique). */
export function recordedTools(conv) {
  const calls = (conv?.turns ?? []).flatMap((t) => t.toolCalls);
  return { count: calls.length, names: uniq(calls.map((c) => c.name)) };
}

/**
 * The New experiment preview line, e.g. `Replays turn 4 of 6 with the
 * recorded history and 3 recorded tool results (lookup_order, issue_refund,
 * escalate_to_human). No live side effects.`
 * @param {object|null} conv  parseConversation result (the sample row)
 * @param {{mode, tools}} replay
 */
export function replayPreview(conv, replay = DEFAULT_REPLAY) {
  const mode = replay?.mode ?? "failing_turn";
  const tools = replay?.tools ?? "recorded";
  const total = conv?.turns?.length ?? 0;
  const turn = conv?.failingTurn ?? total;
  const rec = recordedTools(conv);
  const sideEffects =
    tools === "live" ? "Tools call the live sandbox." : "No live side effects.";
  const toolsText = () => {
    if (tools === "mock") return "mock tool results";
    if (!rec.count) return "no recorded tool calls";
    return `${plural(rec.count, "recorded tool result")} (${rec.names.join(", ")})`;
  };
  if (!total)
    return "Pick a sample row with a conversation to preview the replay.";
  switch (mode) {
    case "score_only":
      return `Scores the ${plural(total, "recorded turn")} as they are. No new calls.`;
    case "full":
      return `Re-simulates the whole conversation from the customer's goal; a simulated customer plays every turn. ${sideEffects}`;
    case "rollout":
      return `Replays from turn ${turn} of ${total}; a simulated customer continues after it, with ${toolsText()}. ${sideEffects}`;
    default:
      return `Replays turn ${turn} of ${total} with the recorded history and ${toolsText()}. ${sideEffects}`;
  }
}

/**
 * The tool chips of a replay vs production: `lookup_order · same call as
 * production`, `issue_refund · new call`, `cancel_item · not called`.
 * @param {{name, args}[]} production  tool calls of the production turn
 * @param {{name, args}[]} replay  tool calls of the replayed turn
 */
export function replayToolChips(production = [], replay = []) {
  const key = (c) =>
    `${c.name}|${JSON.stringify(parseJson(c.args) ?? c.args ?? {})}`;
  const prodKeys = new Set(production.map(key));
  const prodNames = new Set(production.map((c) => c.name));
  const replayNames = new Set(replay.map((c) => c.name));
  const chips = replay.map((c) => {
    if (prodKeys.has(key(c)))
      return {
        name: c.name,
        kind: "same",
        label: `${c.name} · same call as production`,
      };
    if (prodNames.has(c.name))
      return {
        name: c.name,
        kind: "changed",
        label: `${c.name} · different arguments`,
      };
    return { name: c.name, kind: "new", label: `${c.name} · new call` };
  });
  production
    .filter((c) => !replayNames.has(c.name))
    .forEach((c) =>
      chips.push({
        name: c.name,
        kind: "dropped",
        label: `${c.name} · not called`,
      }),
    );
  const seen = new Set();
  return chips.filter((c) => {
    const k = `${c.label}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/**
 * Tool calls of a trace's span tree: `tool` and `retriever` spans, or a
 * known tool name.
 */
export function toolCallsOfSpans(spans, toolNames = []) {
  const known = new Set(toolNames);
  const out = [];
  const walk = (entries) =>
    (entries ?? []).forEach((e) => {
      const s = e?.observation_span ?? {};
      if (
        ["tool", "retriever"].includes(s.observation_type) ||
        known.has(s.name)
      )
        out.push({ name: s.name, args: parseJson(s.input) ?? s.input ?? {} });
      walk(e?.children);
    });
  walk(spans);
  return out;
}

// --------------------------------------------------------------- rows

/** `task` / `conversation` / `trace` / `row`: the row's kind, else the dataset's. */
export function rowKindOf(row, datasetKind) {
  const k = row?.kind ?? row?.row_meta?.kind;
  if (["task", "conversation", "trace", "row"].includes(k)) return k;
  if (datasetKind === "task_suite") return "task";
  if (datasetKind === "conversations") return "conversation";
  return "row";
}

export const sourceKindOf = (source) => {
  const k = source?.kind ?? source?.type ?? null;
  return k === "upload" ? "file" : k;
};

/** True when the original (trace / session) left the retention window. */
export const isExpiredSource = (source) => Boolean(source?.expired);

const short = (id, n = 3) => {
  const s = String(id ?? "");
  return s.length > n ? `${s.slice(0, n)}…` : s;
};

/**
 * The Source chip of a row: `Session chat-1052 ↗`, `Trace 7e1… ↗`, `File
 * support-golden-sep.csv · line 37`, `#2D result`, `Manual`.
 * `{kind, label, icon, link, tooltip, copy}`; `link` = the chip opens
 * something (the arrow is part of the label).
 */
export function sourceChip(source, { rowNumber } = {}) {
  const kind = sourceKindOf(source);
  if (!source || !kind)
    return {
      kind: null,
      label: "—",
      icon: null,
      link: false,
      tooltip: "",
      copy: null,
    };
  switch (kind) {
    case "session": {
      const name = source.session_name ?? short(source.session_id, 8);
      return {
        kind,
        label: `Session ${name} ↗`,
        icon: "mdi:forum-outline",
        link: true,
        tooltip: `${source.project_name ?? "Session"} · ${name}${
          source.expired
            ? " · the original expired; opens the copy saved at import"
            : " · opens the conversation saved at import"
        }`,
        copy: source.session_name ?? source.session_id ?? null,
      };
    }
    case "trace":
    case "span":
      return {
        kind: "trace",
        label: `Trace ${short(source.trace_id)} ↗`,
        icon: "mdi:transit-connection-variant",
        link: Boolean(source.trace_id),
        tooltip: `${source.trace_id ?? "Trace"}${
          source.expired
            ? " · the original expired; opens the copy saved at import"
            : ""
        }`,
        copy: source.trace_id ?? null,
      };
    case "file": {
      const file = source.file_name ?? source.label ?? "Upload";
      const line = Number.isFinite(source.line) ? source.line : rowNumber;
      return {
        kind,
        label: `File ${file}${line ? ` · line ${line}` : ""}`,
        icon: "mdi:file-delimited-outline",
        link: false,
        tooltip: `${file}${line ? ` · line ${line}` : ""}`,
        copy: file,
      };
    }
    case "experiment":
      return {
        kind,
        label: source.run_label
          ? `${source.run_label} result`
          : "Experiment result",
        icon: "material-symbols:science-outline",
        link: Boolean(source.experiment_id),
        tooltip: "Open the experiment result this row came from",
        copy: source.run_label ?? null,
      };
    case "copy":
      return {
        kind,
        label: source.label || "Copied",
        icon: "mdi:content-copy",
        link: Boolean(source.dataset_id),
        tooltip: "Open the source row",
        copy: source.label ?? null,
      };
    case "manual":
      return {
        kind,
        label: "Manual",
        icon: "mdi:account-edit-outline",
        link: false,
        tooltip: "Added by hand",
        copy: null,
      };
    case "sdk":
      return {
        kind,
        label: "SDK",
        icon: "mdi:code-braces",
        link: false,
        tooltip: "Added from the SDK",
        copy: null,
      };
    default:
      return {
        kind,
        label: source.label || kind,
        icon: "mdi:circle-small",
        link: false,
        tooltip: source.label || kind,
        copy: null,
      };
  }
}

// ---------------------------------------------------------------- trace cells

/**
 * A `trace` cell value `{trace_id, name, spans, duration_ms, status}` →
 * `{traceId, text: "support-agent · 9 spans · 2.1 s · OK", status}`.
 */
export function traceItemSummary(value) {
  const v = parseJson(value);
  if (!v || typeof v !== "object") return null;
  const spans = Number(v.spans ?? v.span_count ?? v.total_spans);
  const ms = Number(v.duration_ms ?? v.latency_ms ?? v.total_latency_ms);
  const status = String(v.status ?? "").toUpperCase() || null;
  const parts = [v.name ?? v.trace_name ?? "Trace"];
  if (Number.isFinite(spans)) parts.push(plural(spans, "span"));
  if (Number.isFinite(ms)) parts.push(formatDuration(ms));
  if (status) parts.push(status === "ERROR" ? "Error" : status);
  return {
    traceId: v.trace_id ?? v.id ?? null,
    projectId: v.project_id ?? null,
    text: parts.join(" · "),
    status: status === "ERROR" ? "error" : "ok",
  };
}

/** The text of a span output: a string, else `content` / `text` / `reply` / `output`, else JSON. */
export function outputText(value) {
  if (value == null) return "";
  if (typeof value === "string") {
    const parsed = parseJson(value);
    return parsed && typeof parsed === "object" ? outputText(parsed) : value;
  }
  if (typeof value !== "object") return String(value);
  for (const k of [
    "content",
    "text",
    "reply",
    "answer",
    "output",
    "response",
  ]) {
    if (typeof value[k] === "string") return value[k];
  }
  if (value.message) return outputText(value.message);
  if (Array.isArray(value.choices) && value.choices[0])
    return outputText(value.choices[0].message ?? value.choices[0]);
  return JSON.stringify(value);
}

/** `eval:resolution` → `Resolution`. */
export const metricLabel = (key) => {
  const s = String(key ?? "")
    .replace(/^(eval|check|metric):/, "")
    .replace(/_/g, " ");
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Eval";
};

/** `2.9 s` / `74 s` / `1 min` / `450 ms`. */
export function formatDuration(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  const s = n / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

/**
 * IT-R3 G5 P2: true when a value reads as a conversation (`{turns}`,
 * `{messages}` or `[{role, …}]` with at least one customer / agent turn),
 * whatever its column's data type. Pure.
 */
export function isConversationValue(value) {
  const conv = parseConversation(value);
  if (!conv?.turns?.length) return false;
  const raw = parseJson(value);
  const list = Array.isArray(raw) ? raw : raw?.turns ?? raw?.messages ?? [];
  // Every entry is a message object with a role.
  return list.every(
    (t) => t && typeof t === "object" && typeof t.role === "string",
  );
}

const CONVERSATION_NAME = /conversation|messages|transcript|chat|dialog/i;

/**
 * IT-R3 G5 P2: the row's conversation, wherever it lives: the
 * `conversation` data type first, then a conversation-shaped value in a
 * column named like one (`conversation`, `source_conversation`, `messages`),
 * then any conversation-shaped value. `cells` = useRowCells(): `{byType,
 * byName, columns}`. Returns `{value, column}` or null. Pure.
 */
export function findConversationCell(cells) {
  const typed = cells?.byType?.conversation;
  if (typed && parseConversation(typed.value)) return typed;
  const columns = cells?.columns ?? [];
  const candidates = columns
    .map((column) => ({ column, value: cells?.byName?.[column.name] }))
    .filter(({ value }) => value != null && value !== "")
    .filter(({ value }) => isConversationValue(value));
  return (
    candidates.find(({ column }) => CONVERSATION_NAME.test(column.name)) ??
    candidates[0] ??
    null
  );
}

/**
 * IT-R3 G5 P2: the failing turn's note — `Turn 2 · Resolution failed:
 * promised a callback, no schedule_callback call` — from the conversation
 * verdict, else the row's `production` record. null when nothing failed.
 */
export function failingTurnNote(conv, production) {
  const turn = conv?.failingTurn ?? null;
  const prodTurn = Number(production?.failing_turn);
  const n =
    turn ??
    (Number.isFinite(prodTurn)
      ? conv?.turns?.find((t) => t.sourceN === prodTurn)?.n ?? prodTurn
      : null);
  if (!n) return null;
  const criterion = production?.criterion ?? "Resolution";
  const failed = conv?.resolution === "Fail" || production?.outcome === "fail";
  const reason = String(
    (conv?.resolutionReason || production?.reason) ?? "",
  ).trim();
  if (!failed)
    return { turn: n, text: `Turn ${n} · first turn marked unhelpful` };
  return {
    turn: n,
    text: `Turn ${n} · ${criterion} failed${reason ? `: ${reason}` : ""}`,
  };
}
