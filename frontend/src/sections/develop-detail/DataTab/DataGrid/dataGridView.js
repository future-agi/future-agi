import { formatExactMoney } from "src/sections/common/format/money";
// Pure helpers behind the versioned Data grid (PRODUCT-SPEC §3.3, area-datasets
// §3 G1–G11, §6, §7). No React, no store: the grid, toolbar, header and bulk
// bar import these so every label and filter has one spelling.
// W5 grid seam G2: the `system:expression` filter's one builder.
import { expressionGridFilter } from "src/sections/common/filters/gridDoorModel";

// ------------------------------------------------------------------ G4 density
// R3 (DL#36, DG#15): the G0 DensityMenu values and labels; `full` is
// auto height (R2 stored it as `wrap`).
export const DENSITIES = [
  { value: "compact", label: "Compact (1 line)", hint: "1 line", height: 36 },
  {
    value: "comfortable",
    label: "Comfortable (3 lines)",
    hint: "3 lines",
    height: 76,
  },
  { value: "tall", label: "Tall (6 lines)", hint: "6 lines", height: 136 },
  {
    value: "full",
    label: "Full text (up to 20 lines)",
    hint: "up to 20 lines",
    height: null,
  },
];
export const DEFAULT_DENSITY = "compact";
export const WRAP_MAX_HEIGHT = 320;

/** `wrap` (R2) reads as `full`. */
export const normalizeDensity = (value) => (value === "wrap" ? "full" : value);

export const densityOf = (value) =>
  DENSITIES.find((d) => d.value === normalizeDensity(value)) ?? DENSITIES[0];

/** Fixed row height for a density, or null for auto height (Wrap). */
export const rowHeightFor = (value) => densityOf(value).height;

// --------------------------------------------------------------- G4 col widths
const WIDTH_BY_KIND = {
  text: 240,
  json: 240,
  pass_fail: 160,
  number: 110,
  choices: 170,
  datetime: 150,
  image: 120,
  audio: 220,
};

/**
 * IT-R4 G5 P5: a short-text column whose common values (`stats.top_choices`)
 * cover ≥ 90% of its filled rows fits its longest value (and its header),
 * 104–240 px. Null when the stats cannot tell.
 */
export function shortTextWidth(column) {
  const stats = column?.stats;
  const top = Array.isArray(stats?.top_choices) ? stats.top_choices : null;
  if (!top?.length) return null;
  const filled = Number(stats.total) - Number(stats.empty_count ?? 0);
  const covered = top.reduce((s, c) => s + (Number(c?.count) || 0), 0);
  if (!(filled > 0) || covered < filled * 0.9) return null;
  const longest = Math.max(
    String(column?.name ?? "").length,
    ...top.map((c) => String(c?.value ?? "").length),
  );
  if (longest > 28) return null;
  return Math.min(240, Math.max(104, Math.round(longest * 7.5 + 48)));
}

const SHORT_TEXT_TYPES = new Set(["text", "string", "enum", "categorical"]);

/** One width per column kind (replaces the old minWidth 300 for all). */
export function columnWidthFor(column) {
  const dataType = column?.data_type ?? column?.dataType;
  const origin = column?.origin_type ?? column?.originType;
  const name = String(column?.name ?? column?.headerName ?? "").toLowerCase();
  const stats = column?.stats;
  const total = Number(stats?.total);
  const empty = Number(stats?.empty_count);
  // A Run Prompt column fills as it runs and holds replies: it opens at the
  // text width, not as an empty or short column (options-truth L2 G7).
  if (origin === "run_prompt") return WIDTH_BY_KIND.text;
  if (total > 0 && empty / total > 0.9) return 96;
  if (/^(id|row|uuid)$/.test(name) || /(?:^|_)(id|uuid)$/.test(name))
    return 120;
  const isEval = origin === "evaluation";
  if (!isEval && (!dataType || SHORT_TEXT_TYPES.has(dataType))) {
    const fit = shortTextWidth(column);
    if (fit) return fit;
  }
  if (isEval && dataType === "boolean") return WIDTH_BY_KIND.pass_fail;
  // Eval columns carry a stats line (`81% pass · 47 fail · 3 err`).
  if (isEval && (dataType === "float" || dataType === "integer"))
    return WIDTH_BY_KIND.pass_fail;
  if (isEval && dataType === "array") return WIDTH_BY_KIND.choices;
  if (origin === "evaluation_reason") return WIDTH_BY_KIND.text;
  switch (dataType) {
    case "json":
    case "array":
      return WIDTH_BY_KIND.json;
    case "boolean":
      return WIDTH_BY_KIND.pass_fail;
    case "float":
    case "integer":
      return WIDTH_BY_KIND.number;
    case "datetime":
      return WIDTH_BY_KIND.datetime;
    case "image":
    case "images":
      return WIDTH_BY_KIND.image;
    case "audio":
      return WIDTH_BY_KIND.audio;
    default:
      return WIDTH_BY_KIND.text;
  }
}

// ------------------------------------------------------------------- G3 roles
export const USER_ROLES = ["input", "expected", "metadata"];
export const ROLE_LABEL = {
  input: "Input",
  expected: "Expected output",
  metadata: "Metadata",
};
export const ROLE_TAG = {
  input: "INPUT",
  expected: "EXPECTED",
  metadata: "METADATA",
};

// area-datasets G3 name table, matched per word so `reference_answer` and
// `expected_tool_calls` read as Expected and `customer_tier` as Metadata.
const EXPECTED_WORDS = new Set([
  "expected",
  "answer",
  "reference",
  "ground",
  "groundtruth",
  "target",
  "ideal",
  "label",
  "gold",
  "golden",
]);
const INPUT_WORDS = new Set([
  "input",
  "question",
  "query",
  "prompt",
  "message",
  "messages",
  "conversation",
]);
const METADATA_WORDS = new Set([
  "id",
  "category",
  "difficulty",
  "tier",
  "channel",
  "locale",
  "language",
  "lang",
  "tags",
  "source",
  "split",
  "intent",
]);

/** area-datasets G3: a user column's role from its name. */
export function inferRole(name) {
  const words = String(name ?? "")
    .trim()
    .toLowerCase()
    .split(/[\s_.-]+/)
    .filter(Boolean);
  if (!words.length) return "input";
  if (words.some((w) => w.startsWith("expected") || w.startsWith("ideal")))
    return "expected";
  if (words.some((w) => EXPECTED_WORDS.has(w))) return "expected";
  if (words.some((w) => INPUT_WORDS.has(w))) return "input";
  if (words.some((w) => METADATA_WORDS.has(w))) return "metadata";
  return "input";
}

export const isUserColumn = (column) =>
  (column?.origin_type ?? column?.originType) === "OTHERS";

/**
 * The role a header shows: the stored role for user columns (null = no role),
 * `output` / `eval` / `human` for generated ones.
 */
export function columnRole(column) {
  if (!column) return null;
  if (column.role !== undefined && column.role !== "none") return column.role;
  return null;
}

// Column chooser groups (G8), in display order.
export const COLUMN_GROUPS = [
  { key: "input", label: "Input" },
  { key: "expected", label: "Expected" },
  { key: "metadata", label: "Metadata" },
  { key: "output", label: "Generated outputs" },
  { key: "eval", label: "Evals" },
  { key: "human", label: "Annotations" },
  { key: "none", label: "No role" },
];

export function columnGroupKey(column) {
  const origin = column?.origin_type ?? column?.originType;
  if (origin === "evaluation" || origin === "evaluation_reason") return "eval";
  if (origin === "annotation_label") return "human";
  if (origin && origin !== "OTHERS") return "output";
  const role = columnRole(column);
  return USER_ROLES.includes(role) ? role : "none";
}

/** `[{label, ids}]` for ColumnDropdown's `groups` prop (empty groups dropped). */
export function columnGroups(columns) {
  const buckets = new Map(COLUMN_GROUPS.map((g) => [g.key, []]));
  (columns ?? []).forEach((c) => {
    if ((c?.origin_type ?? c?.originType) === "evaluation_reason") return;
    buckets.get(columnGroupKey(c))?.push(c.id);
  });
  return COLUMN_GROUPS.map((g) => ({
    key: g.key,
    label: g.label,
    ids: buckets.get(g.key),
  })).filter((g) => g.ids.length);
}

// -------------------------------------------------------------- G5 header stats
const pct = (x) => `${Math.round(x * 100)}%`;
const num2 = (x) => (Math.round(x * 100) / 100).toFixed(2);
const secs = (ms) =>
  ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
// v2.1 g8 (dyncol:retrieval): a paid total under a cent reads `<$0.01`
// (embeddings cost millionths), never `$0.00`.
export const money = (x) => formatExactMoney(x);

/**
 * The header's second line for a column, from `column_config[].stats`.
 * Returns `{parts:[{text, tone?, action?}], progress?}` or null.
 *   tone: "error" | "warning" | undefined
 *   action: "failed" | "below" | "errors" | "empty" (what clicking that part filters)
 */
const RUNNING_STATUSES = new Set([
  "Running",
  "running",
  "NotStarted",
  "Queued",
]);

export function statsLine(stats, { status } = {}) {
  if (!stats) return null;
  if (RUNNING_STATUSES.has(status) && stats.total) {
    return {
      parts: [{ text: `Running · ${stats.completed ?? 0}/${stats.total}` }],
      progress: (stats.completed ?? 0) / stats.total,
    };
  }
  const parts = [];
  switch (stats.kind) {
    case "pass_fail":
      if (stats.pass_rate != null)
        parts.push({ text: `${pct(stats.pass_rate)} pass` });
      if (stats.fail_count)
        parts.push({
          text: `${stats.fail_count} fail`,
          tone: "error",
          action: "failed",
        });
      break;
    case "score":
      if (stats.avg != null) parts.push({ text: `avg ${num2(stats.avg)}` });
      if (stats.threshold != null && stats.below_threshold)
        parts.push({
          text: `${stats.below_threshold} < ${stats.threshold}`,
          tone: "error",
          action: "below",
        });
      else if (stats.threshold == null && stats.p50 != null)
        parts.push({ text: `p50 ${num2(stats.p50)}` });
      break;
    case "choices":
      (stats.top_choices ?? []).slice(0, 2).forEach((c) =>
        parts.push({
          text: `${c.value} ${pct(c.count / Math.max(1, stats.total ?? 1))}`,
        }),
      );
      break;
    case "output":
      // L5 G4: a generated column says it finished (`Completed · 248/248`).
      if (stats.completed != null && stats.total)
        parts.push({ text: `Completed · ${stats.completed}/${stats.total}` });
      if (stats.latency_p50_ms != null)
        parts.push({ text: secs(stats.latency_p50_ms) });
      if (stats.cost_total != null)
        parts.push({ text: money(stats.cost_total) });
      break;
    case "expected":
      parts.push(
        stats.empty_count
          ? {
              text: `${stats.empty_count} empty`,
              tone: "warning",
              action: "empty",
            }
          : { text: "None empty" },
      );
      return { parts };
    case "input":
    case "metadata":
      // Only empties are worth a header line; values live in the popover.
      if (stats.empty_count)
        parts.push({ text: `${stats.empty_count} empty`, action: "empty" });
      return parts.length ? { parts } : null;
    default:
      break;
  }
  if (stats.error_count)
    parts.push({
      text: `${stats.error_count} err`,
      tone: "error",
      action: "errors",
    });
  return parts.length ? { parts } : null;
}

// ------------------------------------------------------ G6 / §6 system filters
export const NO_SPLIT = "__none__";

// R2 system filters (R2-SPEC P5 / P6): the same `{column_id, filter_config}`
// shape as `system:row_failed`.
export const SOURCE_KIND_LABEL = Object.freeze({
  session: "Session",
  trace: "Trace",
  file: "File",
  experiment: "Experiment",
  queue: "Queue",
  simulation: "Simulation",
});

/** `system:expression` (W5 grid seam G2): rows the expression keeps. */
export const expressionFilter = expressionGridFilter;

/** `system:row_ids`: exactly these rows; `label` names the set. */
export function rowIdsFilter(ids, { label } = {}) {
  const filterConfig = {
    filter_type: "categorical",
    filter_op: "in",
    filter_value: [...new Set((ids ?? []).filter(Boolean))],
  };
  if (label) filterConfig.label = label;
  return { column_id: "system:row_ids", filter_config: filterConfig };
}

/** `system:source_kind`: rows imported from sessions / traces / files / … */
export const sourceKindFilter = (kind) => ({
  column_id: "system:source_kind",
  filter_config: {
    filter_type: "categorical",
    filter_op: "equals",
    filter_value: kind,
  },
});

/** `system:source_project`: rows imported from one Observe project. */
export function sourceProjectFilter(projectId, { name } = {}) {
  const filterConfig = {
    filter_type: "categorical",
    filter_op: "equals",
    filter_value: projectId,
  };
  if (name) filterConfig.label = name;
  return { column_id: "system:source_project", filter_config: filterConfig };
}

/** `system:invalid`: rows whose values fail their column schema. */
export const invalidFilter = () => ({
  column_id: "system:invalid",
  filter_config: {
    filter_type: "boolean",
    filter_op: "equals",
    filter_value: true,
  },
});

/** W3-3 `system:status`: rows whose status is one of these. */
export const statusFilter = (statuses) => ({
  column_id: "system:status",
  filter_config: {
    filter_type: "categorical",
    filter_op: "in",
    filter_value: [...new Set(statuses ?? [])],
  },
});

/** W3-1 Tags role: rows tagged with any of these (`metadata.tags`). */
export const tagsFilter = (tags) => ({
  column_id: "system:tags",
  filter_config: {
    filter_type: "categorical",
    filter_op: "in",
    filter_value: [...new Set(tags ?? [])],
  },
});

/**
 * W4 §9 (M6): the live collaboration views (`system:assigned_to` me |
 * anyone | nobody | <user id>, `system:open_comments` open | mentions_me,
 * `system:language` <code> | __other__). The server answers them at query
 * time, so the view follows new comments and assignments. `collab` =
 * `{key, value, label}`; the label stays in the view (pill), not the API.
 */
export const COLLAB_VIEW_KEYS = Object.freeze([
  "system:assigned_to",
  "system:open_comments",
  "system:language",
]);
export const collabViewFilter = (collab) => ({
  column_id: collab.key,
  filter_config: {
    filter_type: "categorical",
    filter_op: "in",
    filter_value: [String(collab.value)],
  },
});

/** A live view's pill when its link gave no label. */
export function collabPillLabel(collab) {
  const v = String(collab?.value ?? "");
  if (collab?.key === "system:open_comments")
    return v === "mentions_me"
      ? "Rows that mention you"
      : "Rows with open comments";
  if (collab?.key === "system:assigned_to")
    return v === "me"
      ? "Rows assigned to you"
      : v === "nobody"
        ? "Unassigned rows"
        : v === "anyone"
          ? "Rows assigned to anyone"
          : "Rows assigned to a teammate";
  if (collab?.key === "system:language")
    return v === "__other__"
      ? "Language differs from the dataset"
      : `Language: ${v}`;
  return "Rows";
}

/** `Rows (6)` · `Failure mode: wrong order id (13)`. */
export const rowIdsPillLabel = (ids, label) =>
  `${label || "Rows"} (${(ids ?? []).length.toLocaleString("en-US")})`;
/** `Source: Session`. */
export const sourceKindPillLabel = (kind) =>
  `Source: ${SOURCE_KIND_LABEL[kind] ?? kind ?? "—"}`;
/** `Project: support-prod`. */
export const sourceProjectPillLabel = (projectId, name) =>
  `Project: ${name || projectId || "—"}`;
export const INVALID_PILL_LABEL = "Invalid rows only";

/**
 * API filters for the toolbar's system toggles (CONTRACT.md rule 5 shapes).
 * Pill labels (`rowIdsLabel`, `sourceProjectName`) stay in the view and in
 * drill-down URLs: the API filter_config carries no `label` key, because
 * `serializeFilterForApi` rejects unknown keys.
 */
export function systemFilters({
  failedOnly = false,
  failedEvalIds = [],
  splitIds = [],
  cellErrorOnly = false,
  rowIds = [],
  sourceKind = null,
  sourceProject = null,
  invalidOnly = false,
  statuses = [],
  tags = [],
  collab = null,
  expression = null,
} = {}) {
  const out = [];
  // R3 (DG#12): `Failed only ▾` narrowed to some evals.
  if (failedOnly && failedEvalIds?.length)
    out.push({
      column_id: "system:row_failed_in",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: [...failedEvalIds],
      },
    });
  else if (failedOnly)
    out.push({
      column_id: "system:row_failed",
      filter_config: {
        filter_type: "boolean",
        filter_op: "equals",
        filter_value: true,
      },
    });
  if (cellErrorOnly)
    out.push({
      column_id: "system:cell_error",
      filter_config: {
        filter_type: "boolean",
        filter_op: "equals",
        filter_value: true,
      },
    });
  if (splitIds?.length)
    out.push({
      column_id: "system:splits",
      filter_config: {
        filter_type: "categorical",
        filter_op: "in",
        filter_value: [...splitIds],
      },
    });
  if (rowIds?.length) out.push(rowIdsFilter(rowIds));
  if (sourceKind) out.push(sourceKindFilter(sourceKind));
  if (sourceProject) out.push(sourceProjectFilter(sourceProject));
  if (invalidOnly) out.push(invalidFilter());
  if (statuses?.length) out.push(statusFilter(statuses));
  if (tags?.length) out.push(tagsFilter(tags));
  if (collab?.key && COLLAB_VIEW_KEYS.includes(collab.key))
    out.push(collabViewFilter(collab));
  if (expression) out.push(expressionFilter(expression));
  return out;
}

/**
 * Split an inbound `?filters=` drill-down (grid filter JSON, area-datasets R6)
 * into the toolbar toggles and plain column filters.
 */
export function partitionInboundFilters(list) {
  const result = {
    failedOnly: false,
    cellErrorOnly: false,
    splitIds: [],
    rowIds: [],
    rowIdsLabel: null,
    sourceKind: null,
    sourceProject: null,
    sourceProjectName: null,
    invalidOnly: false,
    statuses: [],
    tags: [],
    collab: null,
    expression: null,
    columnFilters: [],
  };
  (Array.isArray(list) ? list : []).forEach((f) => {
    const config = f?.filter_config ?? {};
    if (f?.column_id === "system:row_failed") {
      result.failedOnly =
        config.filter_op === "not_equals"
          ? !config.filter_value
          : Boolean(config.filter_value);
    } else if (f?.column_id === "system:cell_error") {
      result.cellErrorOnly = Boolean(config.filter_value);
    } else if (f?.column_id === "system:splits") {
      const v = config.filter_value;
      result.splitIds = Array.isArray(v) ? v : v ? [v] : [];
    } else if (f?.column_id === "system:row_ids") {
      const v = config.filter_value;
      result.rowIds = Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];
      result.rowIdsLabel = config.label ?? null;
    } else if (f?.column_id === "system:source_kind") {
      result.sourceKind = config.filter_value || null;
    } else if (f?.column_id === "system:source_project") {
      result.sourceProject = config.filter_value || null;
      result.sourceProjectName = config.label ?? null;
    } else if (f?.column_id === "system:invalid") {
      result.invalidOnly = Boolean(config.filter_value);
    } else if (f?.column_id === "system:status") {
      const v = config.filter_value;
      result.statuses = Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];
    } else if (f?.column_id === "system:tags") {
      const v = config.filter_value;
      result.tags = Array.isArray(v) ? v.filter(Boolean) : v ? [v] : [];
    } else if (COLLAB_VIEW_KEYS.includes(f?.column_id)) {
      const v = config.filter_value;
      const value = Array.isArray(v) ? v[0] : v;
      if (value != null)
        result.collab = {
          key: f.column_id,
          value,
          label: config.label ?? null,
        };
    } else if (f?.column_id === "system:expression") {
      result.expression = config.filter_value || null;
    } else if (f?.column_id) {
      result.columnFilters.push(f);
    }
  });
  return result;
}

/** `All splits` · `hard, smoke` · `3 splits` (area-datasets §6). */
export function splitButtonLabel(selectedIds, splitsById) {
  const ids = selectedIds ?? [];
  if (!ids.length) return "All splits";
  if (ids.length > 2) return `${ids.length} splits`;
  return ids
    .map((id) => (id === NO_SPLIT ? "No split" : splitsById?.[id]?.name))
    .filter(Boolean)
    .join(", ");
}

/** Pill text for the active split filter: `Split: hard, smoke`. */
export const splitPillLabel = (selectedIds, splitsById) =>
  `Split: ${(selectedIds ?? [])
    .map((id) => (id === NO_SPLIT ? "No split" : splitsById?.[id]?.name ?? "—"))
    .join(", ")}`;

/** Split names in the URL (`splits=hard,smoke`) ⇄ ids. */
export function splitIdsFromNames(param, splits) {
  if (param == null || param === "") return [];
  const names = String(param)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return names
    .map((n) =>
      n === "none" ? NO_SPLIT : (splits ?? []).find((s) => s.name === n)?.id,
    )
    .filter(Boolean);
}
export function splitNamesParam(ids, splitsById) {
  const names = (ids ?? [])
    .map((id) => (id === NO_SPLIT ? "none" : splitsById?.[id]?.name))
    .filter(Boolean);
  return names.length ? names.join(",") : null;
}

// ------------------------------------------------------------ §6 tri-state
/**
 * For AssignSplitsMenu: whether every / some / none of the rows carry a split.
 * `rowSplits`: array of split-id arrays, one per selected row.
 */
export function splitCheckState(rowSplits, splitId) {
  const rows = rowSplits ?? [];
  if (!rows.length) return "none";
  const hits = rows.filter((ids) => (ids ?? []).includes(splitId)).length;
  if (hits === 0) return "none";
  return hits === rows.length ? "all" : "some";
}

/** Effective split ids of a row with its pending `set_splits` ops applied. */
export function effectiveSplitIds(baseIds, ops, rowId) {
  let ids = [...(baseIds ?? [])];
  (ops ?? []).forEach((o) => {
    if (o.op !== "set_splits" || !(o.row_ids ?? []).includes(rowId)) return;
    ids = ids.filter((s) => !(o.remove ?? []).includes(s));
    (o.add ?? []).forEach((s) => {
      if (!ids.includes(s)) ids.push(s);
    });
  });
  return ids;
}

// ------------------------------------------------------- §7 selection + G11
/** The bulk bar's count label. */
export function selectionLabel({ selectAll, count, total, filtered }) {
  if (selectAll && count === total) {
    return filtered
      ? `All ${count} matching rows selected`
      : `All ${count} selected`;
  }
  return `${count} selected`;
}

/**
 * `71 of 248 rows (filtered) · 3 selected · v6`; pending new rows (not in
 * `total` until a version saves them) add `+ 1 new, unsaved`.
 */
export function statusBarText({ shown, total, selected, version, added = 0 }) {
  const parts = [
    shown === total || shown == null
      ? `${(total ?? 0).toLocaleString("en-US")} rows`
      : `${shown.toLocaleString("en-US")} of ${(total ?? 0).toLocaleString("en-US")} rows (filtered)`,
  ];
  if (added > 0) parts[0] += ` + ${added.toLocaleString("en-US")} new, unsaved`;
  if (selected) parts.push(`${selected.toLocaleString()} selected`);
  if (version != null) parts.push(`v${version}`);
  return parts.join(" · ");
}

// --------------------------------------------------------- provenance (§8.3)
const SOURCE_ICON = {
  trace: "mdi:transit-connection-variant",
  upload: "mdi:file-upload-outline",
  manual: "mdi:pencil-outline",
  sdk: "mdi:code-braces",
  copy: "mdi:content-copy",
  experiment: "mdi:flask-outline",
  synthetic: "mdi:creation-outline",
  huggingface: "mdi:emoticon-happy-outline",
};

/** `{label, icon, kind}` for row_meta.source. */
export function sourceLabel(source) {
  if (!source) return { label: "—", icon: null, kind: null };
  const kind = source.type ?? null;
  const icon = SOURCE_ICON[kind] ?? "mdi:circle-small";
  switch (kind) {
    case "trace":
      return { label: "Trace", icon, kind };
    case "upload":
      return {
        label: source.file_name || source.label || "Upload",
        icon,
        kind,
      };
    case "manual":
      return { label: "Manual", icon, kind };
    case "sdk":
      return { label: "SDK", icon, kind };
    case "copy":
      return { label: source.label || "Copied", icon, kind };
    case "experiment":
      return {
        label: source.run_label
          ? `Experiment ${source.run_label}`
          : "Experiment",
        icon,
        kind,
      };
    default:
      return { label: source.label || kind || "—", icon, kind };
  }
}

// ------------------------------------------------------ per-dataset storage
export const storageKey = (datasetId, what) => `fa.ds.${datasetId}.${what}`;
export const DEFAULT_SYSTEM_COLS = {
  splits: true,
  source: true,
  updated: false,
};

export function readStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}
export function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage unavailable (private window): the view just doesn't persist
  }
}
