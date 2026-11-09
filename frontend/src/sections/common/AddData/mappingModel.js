// Pure helpers of the Add-data sheet (R2-SPEC §7.3): the IM1 request body,
// the default mapping per column, the one-line copy (auto-map, summary,
// filter, description), item summaries, edits applied to commit
// operations, the remembered mapping and `Copy as API call`.
import { plural } from "src/sections/common/vocab";
import {
  fiRequest,
  requestSnippets,
} from "src/sections/common/commands/requestSnippets";
import { INPUT_SYNONYMS } from "src/sections/common/RunSources/runOnRows"; // R4-F frozen line
import {
  EXPECTED_OPTIONS,
  INCLUDE_FLAGS,
  RESOLUTION_FILTERS,
  WINDOW_LABEL,
  fieldLabel,
  im1Shape,
  newDatasetKind,
  shapesFor,
} from "./shapes";

export const IMPORTS_PATH = "/model-hub/datasets/imports/";
export const PREVIEW_SAMPLE = 6;
export const CLIP_PREVIEW = 80;

// ----------------------------------------------------------------- text

/** Clip to `n` chars with an ellipsis; non-strings are stringified. */
export function clipText(value, n = CLIP_PREVIEW) {
  if (value == null) return "";
  let s;
  if (typeof value === "string") s = value;
  else {
    try {
      s = JSON.stringify(value);
    } catch {
      s = String(value);
    }
  }
  s = (s ?? "").replace(/\s+/g, " ").trim();
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export const isEmptyValue = (v) =>
  v == null ||
  (typeof v === "string" && !v.trim()) ||
  (Array.isArray(v) && v.length === 0) ||
  (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0);

// ------------------------------------------------------- conversations

/** `{turns:[…]}` values (the `conversation` data type), else null. */
export const asConversation = (v) =>
  v && typeof v === "object" && Array.isArray(v.turns) ? v : null;

/** The first conversation-like cell of an item. */
export function conversationOfCells(cells = {}) {
  const keys = ["conversation", "source_conversation"];
  for (const k of keys) if (asConversation(cells[k])) return cells[k];
  return Object.values(cells).find((v) => asConversation(v)) ?? null;
}

/** The opening customer message of a conversation ('' when none). */
export function openingMessage(conv) {
  const turns = asConversation(conv)?.turns ?? [];
  const first = turns.find((t) => t.role === "customer" || t.role === "user");
  return typeof first?.content === "string" ? first.content : "";
}

/**
 * `{turns, toolCalls, ends, dots, text}` for a conversation: `6 turns ·
 * 3 tool calls · ends: "…"` (ends = the last customer message) and one dot
 * per turn (`pass` / `fail` from the per-turn eval, else `none`).
 */
export function conversationSummary(conv, { endsChars = 40 } = {}) {
  const turns = asConversation(conv)?.turns ?? [];
  const toolCalls = turns.reduce((n, t) => n + (t.tool_calls?.length ?? 0), 0);
  const lastCustomer = [...turns]
    .reverse()
    .find((t) => t.role === "customer" || t.role === "user");
  const ends = clipText(lastCustomer?.content ?? "", endsChars);
  const dots = turns.map((t) => {
    const v = t.evals?.helpfulness ?? t.eval ?? null;
    if (v === "Pass" || v === true) return "pass";
    if (v === "Fail" || v === false) return "fail";
    return "none";
  });
  const parts = [plural(turns.length, "turn"), plural(toolCalls, "tool call")];
  if (ends) parts.push(`ends: "${ends}"`);
  return {
    turns: turns.length,
    toolCalls,
    ends,
    dots,
    text: parts.join(" · "),
  };
}

/** `5 checks` for a checks cell. */
export function checksSummary(checks) {
  const list = Array.isArray(checks) ? checks : [];
  return {
    count: list.length,
    draft: list.some((c) => c?.draft),
    text: plural(list.length, "check"),
  };
}

/** `1 order · A-8830` for a start-state cell. */
export function startStateSummary(state) {
  if (!state || typeof state !== "object") return clipText(state, 40);
  const orders = Array.isArray(state.orders) ? state.orders : [];
  const keys = Object.keys(state);
  if (orders.length)
    return `${plural(orders.length, "order")} · ${orders
      .map((o) => o?.id)
      .filter(Boolean)
      .slice(0, 2)
      .join(", ")}`;
  return keys.length ? keys.join(", ") : "empty";
}

/** `support-agent · 4 spans · 2.1 s · OK` from a trace read. */
export function traceItemLabel(read) {
  if (!read) return null;
  const name =
    read.trace?.name ??
    read.observation_spans?.[0]?.observation_span?.name ??
    "trace";
  const spans = read.summary?.total_spans ?? 0;
  const ms = read.summary?.total_latency_ms;
  const errors = read.summary?.error_count ?? 0;
  const parts = [name, plural(spans, "span")];
  if (Number.isFinite(ms)) parts.push(formatDuration(ms));
  parts.push(errors ? plural(errors, "error") : "OK");
  return parts.join(" · ");
}

/** `412 ms` / `2.1 s` / `74 s` / `2 min`. */
export function formatDuration(ms) {
  if (!Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 120) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

// -------------------------------------------------------------- mapping

const TASK_AUTO = {
  title: { label: "Drafted from the session" },
  instruction: { label: "First customer message" },
  customer_goal: { label: "Drafted from the session", draft: true },
  order_id: { label: "Order id in the conversation" },
  start_state: { label: "Orders seen by lookup_order" },
  checks: { label: "Check evals drafted from the session", draft: true },
  category: { label: "Session intent" },
  difficulty: { label: "Drafted from the session" },
  allowed_tools: { label: "Tools the agent used" },
  source_conversation: { label: "Whole session (reference only)" },
};

const SIMULATION_AUTO = {
  conversation: { label: "Transcript (with tool calls)" },
  source_conversation: { label: "Transcript (with tool calls)" },
  customer_goal: { label: "Scenario goal" },
  metadata: { label: "Persona, scenario, verdict" },
};

const QUEUE_AUTO = {
  metadata: { label: "Labels and notes" },
};

const expectedColumn = (name) => /^expected/i.test(name);

/** The server's default `from` for one target column (display only). */
export function autoSourceFor(name, { sourceKind, shapeId, expected }) {
  const key = String(name).toLowerCase();
  if (expectedColumn(key)) {
    if (sourceKind === "queue")
      return { label: "Reviewer correction (else empty)", empty: true };
    if (expected === "production_output")
      return { label: "Production reply", warn: true };
    if (expected === "human_correction")
      return { label: "Human correction, else empty", empty: true };
    return { label: "Leave empty (needs review)", empty: true };
  }
  if (sourceKind === "simulation" && SIMULATION_AUTO[key])
    return SIMULATION_AUTO[key];
  if (sourceKind === "queue" && QUEUE_AUTO[key]) return QUEUE_AUTO[key];
  if (shapeId === "task" && TASK_AUTO[key]) return TASK_AUTO[key];
  if (key === "title") return { label: "First customer message (80 chars)" };
  if (INPUT_SYNONYMS.includes(key))
    // R4-F frozen line (D24)
    return {
      label: sourceKind === "traces" ? "Trace input" : "First customer message",
    };
  if (key === "conversation" || key === "source_conversation")
    return {
      label:
        shapeId === "failing_turn"
          ? "Conversation up to the failing turn"
          : "Whole conversation",
    };
  if (key === "order_id") return { label: "Order id in the conversation" };
  if (key === "turn") return { label: "Agent turn number" };
  if (key === "failing_turn") return { label: "Failing turn number" };
  if (key === "category" || key === "intent")
    return { label: "Session intent" };
  return null;
}

/**
 * One mapping row per target column: `{target, auto, override, filled,
 * unmapped, value}` where `value` is row 1's preview value.
 */
export function mappingRows({
  columns = [],
  items = [],
  overrides = {},
  sourceKind,
  shapeId,
  expected,
}) {
  return columns.map((target) => {
    const auto = autoSourceFor(target, { sourceKind, shapeId, expected });
    const override = overrides[target] ?? null;
    const values = items.map((i) => i?.cells?.[target]);
    const filled = values.some((v) => !isEmptyValue(v));
    const intentionallyEmpty =
      override?.mode === "empty" || (!override && auto?.empty);
    return {
      target,
      auto,
      override,
      filled,
      unmapped: !filled && !intentionallyEmpty,
      value: values[0],
    };
  });
}

const TASK_KEYS = [
  "title",
  "instruction",
  "customer_goal",
  "start_state",
  "checks",
];

/**
 * `Fills title, instruction, customer_goal, start_state, checks · 0
 * unmapped`. Task shapes name the five task fields; others the first four
 * filled columns (`+2 more`).
 */
export function autoMapLine(rows, { shapeId } = {}) {
  const filled = rows.filter((r) => r.filled).map((r) => r.target);
  const unmapped = rows.filter((r) => r.unmapped).length;
  let names;
  if (shapeId === "task") names = TASK_KEYS.filter((k) => filled.includes(k));
  else {
    const shown = filled.slice(0, 4);
    names =
      filled.length > 4 ? [...shown, `+${filled.length - 4} more`] : shown;
  }
  const head = names.length ? `Fills ${names.join(", ")}` : "Fills nothing yet";
  // IT-R2 G5 P3: plain words, not `4 unmapped`.
  return unmapped
    ? `${head} · ${unmapped} ${unmapped === 1 ? "column stays" : "columns stay"} empty`
    : `${head} · every column filled`;
}

/** The IM1 `mapping` array from the user's overrides (auto rows omitted). */
export function mappingBody(overrides = {}) {
  return Object.entries(overrides)
    .map(([target, o]) => {
      if (!o) return null;
      if (o.mode === "empty") return { target, from: { empty: true } };
      if (o.mode === "field" && o.field)
        return { target, from: { field: o.field } };
      if (o.mode === "path" && o.path && !o.error)
        return { target, from: { path: o.path } };
      return null;
    })
    .filter(Boolean);
}

/** `Field ▾` / `Path` / `Leave empty` label of a row's `from`. */
export function fromLabel(row) {
  const o = row.override;
  if (o?.mode === "empty") return "Leave empty";
  if (o?.mode === "path") return o.path || "Path";
  if (o?.mode === "field") return fieldLabel(o.field);
  return row.auto?.label ?? "Not mapped";
}

// ------------------------------------------------------------- request

/** Observe `filters` for the sessions filter (Resolution). */
// W3-5: the windows an import can read (`Last 24 hours … 30 days`).
export const IMPORT_WINDOW_OPTIONS = Object.freeze([
  { id: "24h", label: "Last 24 hours", days: 1 },
  { id: "7d", label: "Last 7 days", days: 7 },
  { id: "14d", label: "Last 14 days", days: 14 },
  { id: "30d", label: "Last 30 days", days: 30 },
]);

const shortDay = (iso) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

/** The window chip: `Last 7 days`, or `Sep 15 – Oct 22` for a custom range. */
export function windowChipLabel(window) {
  if (!window) return null;
  if (typeof window === "object")
    return window.from && window.to
      ? `${shortDay(window.from)} – ${shortDay(window.to)}`
      : null;
  return IMPORT_WINDOW_OPTIONS.find((w) => w.id === window)?.label ?? null;
}

const res0 = (resolution) => {
  const r = RESOLUTION_FILTERS.find((x) => x.id === resolution);
  return r && r.id !== "any" ? r.label : null;
};

export function sessionFilters(resolution) {
  return resolution === "Fail"
    ? [{ column: "Resolution", operator: "equals", value: "Fail" }]
    : [];
}

/**
 * The IM1 body (without `op`) for the sheet state.
 * @param {object} s
 */
export function importBody({
  sourceKind,
  projectId,
  sessionIds,
  traceIds,
  resolution,
  queueId,
  completedOnly = true,
  shapeId,
  overrides,
  includes,
  dedupe = true,
  expected = "empty",
  target,
  split,
  sample,
  tempIds,
  window = null,
}) {
  let source;
  if (sourceKind === "traces")
    source = traceIds?.length
      ? { kind: "traces", project_id: projectId, trace_ids: traceIds }
      : {
          // W3-5: traces honour the same filters as sessions and read every
          // match (no default cap; the preview counts first).
          kind: "traces",
          project_id: projectId,
          filters: sessionFilters(resolution),
          ...(window ? { window } : {}),
        };
  else if (sourceKind === "queue")
    source = {
      kind: "queue",
      queue_id: queueId,
      completed_only: completedOnly,
    };
  else if (sourceKind === "simulation")
    source = { kind: "simulation", simulation_run_id: null };
  else
    source = sessionIds?.length
      ? { kind: "sessions", project_id: projectId, session_ids: sessionIds }
      : {
          kind: "sessions",
          project_id: projectId,
          filters: sessionFilters(resolution),
          window: window ?? "7d",
        };
  const body = {
    source,
    shape: im1Shape(sourceKind, shapeId),
    mapping: mappingBody(overrides),
    expected: sourceKind === "queue" ? "human_correction" : expected,
    target: target ?? {},
  };
  if (sourceKind === "traces") {
    body.granularity = shapeId;
    if (shapeId === "per_span") body.span_filter = { name: "respond" };
    body.include = { ...(includes ?? {}) };
    body.dedupe = dedupe ? "source_trace_id" : "none";
  }
  if (split) body.split = split;
  if (sample) body.sample = sample;
  if (tempIds) body.temp_ids = tempIds;
  return body;
}

/** The IM1 `target` from the sheet's target state (`{dataset_id}` | new). */
export function targetBody(target) {
  if (!target) return {};
  if (target.dataset_id) return { dataset_id: target.dataset_id };
  const spec = target.new ?? target.new_dataset;
  if (spec)
    return { new: { name: spec.name ?? "", kind: spec.kind ?? "rows" } };
  return {};
}

/** Normalise `openAddData`'s target (`{dataset_id}` | `{new}` | `{new_dataset}`). */
export function normaliseTarget(target, shapeId) {
  if (target?.dataset_id) return { dataset_id: target.dataset_id };
  const spec = target?.new ?? target?.new_dataset;
  if (spec)
    return {
      new: {
        name: spec.name ?? "",
        kind: spec.kind ?? newDatasetKind(shapeId),
      },
    };
  return null;
}

// ---------------------------------------------------------------- copy

/** `support-prod · last 7 days · Resolution = Fail · 6 sessions`. */
export function filterSummary({
  sourceKind,
  projectName,
  pinnedCount = 0,
  resolution,
  count,
  queueName,
  completedOnly,
  windowLabel = WINDOW_LABEL,
}) {
  const n = Number.isFinite(count) ? count : null;
  const project = projectName || "Project";
  if (sourceKind === "sessions") {
    if (pinnedCount) return `${project} · ${plural(pinnedCount, "session")}`;
    const res = RESOLUTION_FILTERS.find((r) => r.id === resolution);
    const parts = [project, windowLabel];
    if (res && res.id !== "any") parts.push(res.label);
    if (n != null) parts.push(plural(n, "session"));
    return parts.join(" · ");
  }
  if (sourceKind === "traces") {
    if (pinnedCount) return `${project} · ${plural(pinnedCount, "trace")}`;
    const parts = [project, windowLabel];
    if (res0(resolution)) parts.push(res0(resolution));
    if (n != null) parts.push(plural(n, "trace"));
    return parts.join(" · ");
  }
  if (sourceKind === "queue") {
    if (!queueName) return "Pick a queue";
    const parts = [queueName];
    if (completedOnly) parts.push("completed items only");
    if (n != null) parts.push(plural(n, "item"));
    return parts.join(" · ");
  }
  return "";
}

/** `1,240 traces match · Add all (background job) · First 200 · …`. */
export function largeSelectionText(count, noun = "trace") {
  return `${plural(count, noun)} match`;
}

/**
 * options-truth L5 G5 (W0-6): `first 200 of 1,240 matching` when the
 * preview stopped at its cap (`{matching, capped_at}` from IM1), else null.
 */
export function cappedText(preview) {
  const matching = Number(preview?.matching);
  const cap = Number(preview?.capped_at);
  if (!Number.isFinite(matching) || !Number.isFinite(cap) || !cap) return null;
  if (matching <= cap) return null;
  return `first ${cap.toLocaleString("en-US")} of ${matching.toLocaleString("en-US")} matching`;
}

/**
 * W3-5: above 1,000 items the preview lists the first page:
 * `Showing the first 100 of 1,296 · the import runs in the background`.
 */
export function pagedText(preview) {
  if (!preview?.paged) return null;
  const shown = preview.items?.length ?? 0;
  const total = Number(preview.total ?? preview.matching);
  if (!Number.isFinite(total)) return null;
  return `Showing the first ${shown.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} · the import runs in the background`;
}

/**
 * Whether an item goes in: a new item unless the user unticked it, a
 * duplicate only when the user ticked it to add it again.
 */
export const isIncluded = (
  item,
  unchecked = new Set(),
  addAgain = new Set(),
) =>
  item?.status === "new"
    ? !unchecked.has(item.temp_id)
    : item?.status === "duplicate"
      ? addAgain.has(item.temp_id)
      : false;

/** The temp ids the commit adds (new items kept + duplicates ticked). */
export const includedTempIds = (items = [], unchecked, addAgain) =>
  items.filter((i) => isIncluded(i, unchecked, addAgain)).map((i) => i.temp_id);

/**
 * Summary counts with the user's unticked items taken out and the
 * duplicates they ticked moved from skip to add: `{add, skip, needsReview,
 * rejected}`. `items` are the sampled preview items.
 */
export function summaryCounts(
  summary,
  items = [],
  unchecked = new Set(),
  addAgain = new Set(),
) {
  const s = summary ?? {};
  const off = items.filter(
    (i) => i.status === "new" && unchecked.has(i.temp_id),
  );
  const again = items.filter(
    (i) => i.status === "duplicate" && addAgain.has(i.temp_id),
  ).length;
  const offReview = off.filter((i) => i.needs_review).length;
  return {
    add: Math.max(0, (s.add ?? 0) - off.length) + again,
    skip: Math.max(0, (s.skip_duplicates ?? 0) - again),
    needsReview: Math.max(0, (s.needs_review ?? 0) - offReview),
    rejected: s.rejected ?? 0,
  };
}

/** `Add 6 rows · skip 0 duplicates (already imported) · 6 need review`. */
export function summaryParts(counts) {
  return [
    { key: "add", text: `Add ${plural(counts.add, "row")}` },
    {
      key: "skip",
      text: `skip ${plural(counts.skip, "duplicate")} (already imported)`,
    },
    // IT-R2 G5 P3: what "review" means (fields the import guessed).
    ...(counts.needsReview
      ? [
          {
            key: "review",
            text: `${plural(counts.needsReview, "row")} ${counts.needsReview === 1 ? "needs" : "need"} a quick look (fields we guessed)`,
          },
        ]
      : []),
    ...(counts.rejected
      ? [
          {
            key: "rejected",
            text: `${counts.rejected.toLocaleString("en-US")} rejected`,
          },
        ]
      : []),
  ];
}

export const summaryText = (counts) =>
  summaryParts(counts)
    .map((p) => p.text)
    .join(" · ");

const SHAPE_WORDS = {
  task: "as tasks",
  conversation: "as conversations",
  per_turn: "one row per turn",
  failing_turn: "as failing turns",
  per_trace: "one row per trace",
  per_span: "one row per span",
  trace_item: "as trace items",
};

/**
 * Save-version description: `Added 6 rows from support-prod sessions as
 * tasks (Resolution = Fail, last 7 days)`.
 */
export function importDescription({
  count,
  projectName,
  sourceKind,
  shapeId,
  resolution,
  pinned,
  queueName,
  windowLabel,
}) {
  const rows = plural(count, "row");
  if (sourceKind === "queue")
    return `Added ${rows} from ${queueName ?? "an annotation queue"} (reviewer corrections as expected)`;
  const noun = sourceKind === "traces" ? "traces" : "sessions";
  const head =
    `Added ${rows} from ${projectName ?? "production"} ${noun} ${SHAPE_WORDS[shapeId] ?? ""}`.trim();
  if (pinned) return `${head} (selected in Observe)`;
  const res =
    resolution && resolution !== "any" ? `Resolution = ${resolution}, ` : "";
  return `${head} (${res}${windowLabel ?? WINDOW_LABEL})`;
}

/** `≈ 9.6 KB per trace · 826 KB total` for the include flags. */
export function includeEstimate(includes = {}, count = 0) {
  const kb =
    1.1 +
    INCLUDE_FLAGS.filter((f) => includes[f.id]).reduce((n, f) => n + f.kb, 0);
  const total = kb * Math.max(0, count);
  const fmt = (v) =>
    v >= 1024
      ? `${(v / 1024).toFixed(1)} MB`
      : `${v < 10 ? v.toFixed(1) : Math.round(v)} KB`;
  return `≈ ${fmt(kb)} per trace · ${fmt(total)} total`;
}

export const expectedLabel = (id) =>
  EXPECTED_OPTIONS.find((o) => o.id === id)?.label ?? EXPECTED_OPTIONS[0].label;

/** Whether a shape needs a task-suite target (or a new one). */
export function targetProblem({ shapeId, sourceKind, targetKind, isNew }) {
  if (shapeId === "task" && !isNew && targetKind !== "task_suite")
    return "As tasks needs a task suite. Pick one, or create a new dataset.";
  if (
    shapesFor(sourceKind).every((s) => s.id !== shapeId) &&
    sourceKind !== "file" &&
    sourceKind !== "sdk"
  )
    return "Pick a shape.";
  return null;
}

// --------------------------------------------------------------- commit

/**
 * IM1 commit operations → change-set `add_row` ops: cells by column id,
 * preview edits applied (by column name), unique temp ids, the import
 * description carried as `description_hint`.
 */
export function toChangeSetOps(
  operations = [],
  {
    edits = {},
    columnIdByName = {},
    splitIds = [],
    hint,
    kind,
    stamp = "",
  } = {},
) {
  return operations.map((op, i) => {
    const cells = { ...(op.cells_by_column_id ?? {}) };
    Object.entries(edits[op.temp_id] ?? {}).forEach(([name, value]) => {
      const id = columnIdByName[name];
      if (id) cells[id] = value;
    });
    return {
      op: "add_row",
      temp_id: `imp-${stamp}${i + 1}`,
      kind: kind ?? op.kind ?? "row",
      source: op.source ?? null,
      expected_source: op.expected_source ?? "empty",
      // IT-R2 G5 P1 (S2): what failed in production rides to the row.
      ...(op.production ? { production: op.production } : {}),
      // W3-3: production rows arrive as Needs review.
      ...(op.status ? { status: op.status } : {}),
      // W3-5: trace / session metadata merged into the row's metadata.
      ...(op.metadata ? { metadata: op.metadata } : {}),
      cells,
      split_ids: splitIds,
      ...(hint ? { description_hint: hint } : {}),
    };
  });
}

// ------------------------------------------------------- remember mapping

export const rememberKey = (projectId, datasetId) =>
  `fa.addData.mapping.${projectId ?? "none"}.${datasetId ?? "new"}`;

export function loadMapping(projectId, datasetId, storage) {
  try {
    const s = storage ?? window.localStorage;
    const raw = s.getItem(rememberKey(projectId, datasetId));
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

export function saveMapping(projectId, datasetId, overrides, storage) {
  try {
    const s = storage ?? window.localStorage;
    const key = rememberKey(projectId, datasetId);
    if (overrides && Object.keys(overrides).length)
      s.setItem(key, JSON.stringify(overrides));
    else s.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

// ------------------------------------------------------- Copy as API call

/** A Python literal for a JSON value (4-space indent). */
export function toPython(value, depth = 0) {
  const pad = (d) => "    ".repeat(d);
  if (value === null || value === undefined) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "number")
    return Number.isFinite(value) ? String(value) : "None";
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    return `[\n${value.map((v) => `${pad(depth + 1)}${toPython(v, depth + 1)}`).join(",\n")},\n${pad(depth)}]`;
  }
  const entries = Object.entries(value);
  if (!entries.length) return "{}";
  return `{\n${entries
    .map(
      ([k, v]) =>
        `${pad(depth + 1)}${JSON.stringify(k)}: ${toPython(v, depth + 1)}`,
    )
    .join(",\n")},\n${pad(depth)}}`;
}

/**
 * cURL, Python and TypeScript for the IM1 commit, built by the one command
 * builder from exactly the request `useImportCommit` sends
 * (`{...body, op: "commit"}` to IMPORTS_PATH).
 * L7b G8: not mounted. The Add data sheet shows no API call until the
 * backend serves IM1 (or the SDK ships a dataset import call printed
 * identically); kept so the one command builder's tests keep its shape.
 */
export function apiCallSnippets(body) {
  // A preview-only `sample` never reaches a commit.
  const { sample: _sample, ...sent } = body ?? {};
  return requestSnippets(fiRequest(IMPORTS_PATH, { ...sent, op: "commit" }));
}
