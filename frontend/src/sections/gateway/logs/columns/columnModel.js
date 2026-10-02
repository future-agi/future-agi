// Column model for the Request Logs table (TH-7041).
//
// One ordered, versioned configuration drives header, rows, skeleton and the
// empty-state colSpan. Built-in ids are namespaced `builtin:<id>` and declared
// custom-property ids `metadata:<name>` so a property called "model" can never
// collide with the built-in Model column.

import { REQUEST_TAG } from "../../constants/requestTags";

export const CONFIG_VERSION = 1;
export const COLUMN_VIEW_ID = "requests";
export const MAX_COLUMN_ENTRIES = 64;
export const CUSTOM_COLUMN_WIDTH = 140;
export const PICKER_SEARCH_THRESHOLD = 8;
export const COLUMNS_POPOVER_ID = "request-columns-popover";

const BUILTIN_PREFIX = "builtin:";
const METADATA_PREFIX = "metadata:";

// Same validation rule the backend applies to custom property names
// (futureagi/agentcc/validators.py); the frontend still treats names as text.
const METADATA_NAME_RE = /^[A-Za-z0-9_-]{1,255}$/;

// The ten existing columns, in the existing default order (R43 / AC1).
export const BUILTIN_COLUMNS = [
  {
    key: "startedAt",
    label: "Timestamp",
    width: 180,
    sortable: true,
    locked: true,
  },
  { key: "model", label: "Model", width: 140, sortable: false },
  { key: "provider", label: "Provider", width: 120, sortable: false },
  { key: "application", label: "Application", width: 130, sortable: false },
  { key: "service", label: "Service", width: 130, sortable: false },
  { key: "statusCode", label: "Status", width: 80, sortable: true },
  { key: "latencyMs", label: "Latency", width: 100, sortable: true },
  { key: "cost", label: "Cost", width: 100, sortable: true },
  { key: "totalTokens", label: "Tokens", width: 120, sortable: true },
  { key: "sessionId", label: "Session ID", width: 130, sortable: false },
].map((col) => ({
  ...col,
  id: `${BUILTIN_PREFIX}${col.key}`,
  kind: "builtin",
  locked: Boolean(col.locked),
}));

const BUILTIN_BY_ID = new Map(BUILTIN_COLUMNS.map((col) => [col.id, col]));

export const LOCKED_COLUMN_ID = BUILTIN_COLUMNS[0].id;

export const DEFAULT_CONFIG = Object.freeze({
  v: CONFIG_VERSION,
  columns: BUILTIN_COLUMNS.map((col) => ({ id: col.id })),
  hidden: [],
});

// Application and Service are already built-in columns (D3).
const RESERVED_METADATA_NAMES = new Set([
  REQUEST_TAG.APPLICATION,
  REQUEST_TAG.SERVICE,
]);

export function toCustomColumnId(name) {
  return `${METADATA_PREFIX}${name}`;
}

export function isCustomColumnId(id) {
  return typeof id === "string" && id.startsWith(METADATA_PREFIX);
}

export function customNameFromId(id) {
  return isCustomColumnId(id) ? id.slice(METADATA_PREFIX.length) : null;
}

function isPlainObject(value) {
  return Object.prototype.toString.call(value) === "[object Object]";
}

function isKnownId(id) {
  if (typeof id !== "string") return false;
  if (BUILTIN_BY_ID.has(id)) return true;
  const name = customNameFromId(id);
  return name !== null && METADATA_NAME_RE.test(name);
}

/**
 * Validate a parsed storage record. Any defect rejects the whole record and
 * the caller falls back to DEFAULT_CONFIG (R14/R15). Returns a normalized
 * config that holds ids only (R13) or null.
 */
export function validateConfig(raw) {
  if (!isPlainObject(raw)) return null;
  if (raw.v !== CONFIG_VERSION) return null;
  if (!Array.isArray(raw.columns) || !Array.isArray(raw.hidden)) return null;
  if (raw.columns.length < 1 || raw.columns.length > MAX_COLUMN_ENTRIES) {
    return null;
  }

  const seen = new Set();
  const columns = [];
  for (const entry of raw.columns) {
    if (!isPlainObject(entry) || !isKnownId(entry.id)) return null;
    if (seen.has(entry.id)) return null;
    seen.add(entry.id);
    columns.push({ id: entry.id });
  }

  if (columns[0].id !== LOCKED_COLUMN_ID) return null;

  const hidden = [];
  for (const id of raw.hidden) {
    if (typeof id !== "string" || !seen.has(id)) return null;
    if (id === LOCKED_COLUMN_ID) return null;
    if (!hidden.includes(id)) hidden.push(id);
  }

  // Forward compatibility: a built-in added later appears visible at the end,
  // keeping the default relative order.
  for (const col of BUILTIN_COLUMNS) {
    if (!seen.has(col.id)) columns.push({ id: col.id });
  }

  return { v: CONFIG_VERSION, columns, hidden };
}

function declaredNames(declarations) {
  const names = [];
  for (const item of Array.isArray(declarations) ? declarations : []) {
    const name = typeof item === "string" ? item : item?.name;
    if (
      typeof name === "string" &&
      METADATA_NAME_RE.test(name) &&
      !RESERVED_METADATA_NAMES.has(name) &&
      !names.includes(name)
    ) {
      names.push(name);
    }
  }
  return names;
}

function customColumn(name) {
  return {
    id: toCustomColumnId(name),
    kind: "metadata",
    name,
    label: name,
    width: CUSTOM_COLUMN_WIDTH,
    sortable: false,
    locked: false,
  };
}

/**
 * Resolve the visible ordered columns plus picker entries from a config, the
 * current-org declarations and the declaration query status.
 *
 * Custom columns render only while `status === "success"` (J6/AC18); saved ids
 * that a successful fetch does not declare are reported as `stale` (J5/AC14).
 */
export function resolveColumns({ config, declarations, status }) {
  const safeConfig = config || DEFAULT_CONFIG;
  const hidden = new Set(safeConfig.hidden);
  const declared = status === "success" ? declaredNames(declarations) : [];
  const declaredSet = new Set(declared);

  const columns = [];
  const builtinEntries = [];
  const customEntries = [];
  const stale = [];
  const inConfig = new Set();

  safeConfig.columns.forEach((entry) => {
    inConfig.add(entry.id);
    const builtin = BUILTIN_BY_ID.get(entry.id);
    if (builtin) {
      const checked = builtin.locked || !hidden.has(builtin.id);
      if (checked) columns.push(builtin);
      builtinEntries.push({
        id: builtin.id,
        kind: "builtin",
        label: builtin.label,
        locked: builtin.locked,
        checked,
        inConfig: true,
      });
      return;
    }
    const name = customNameFromId(entry.id);
    if (name === null) return;
    if (status !== "success") return;
    if (!declaredSet.has(name)) {
      stale.push(entry.id);
      return;
    }
    const col = customColumn(name);
    const checked = !hidden.has(col.id);
    if (checked) columns.push(col);
    customEntries.push({
      id: col.id,
      kind: "metadata",
      label: col.label,
      locked: false,
      checked,
      inConfig: true,
    });
  });

  declared.forEach((name) => {
    const id = toCustomColumnId(name);
    if (inConfig.has(id)) return;
    customEntries.push({
      id,
      kind: "metadata",
      label: name,
      locked: false,
      checked: false,
      inConfig: false,
    });
  });

  const withMoves = (entries) =>
    entries.map((entry, index) => {
      const movable = entry.inConfig && !entry.locked;
      const prev = entries[index - 1];
      const next = entries[index + 1];
      return {
        ...entry,
        canMoveUp: movable && Boolean(prev) && !prev.locked && prev.inConfig,
        canMoveDown: movable && Boolean(next) && next.inConfig,
      };
    });

  const entries = {
    builtin: withMoves(builtinEntries),
    custom: withMoves(customEntries),
  };
  const all = [...entries.builtin, ...entries.custom];

  return {
    columns,
    entries,
    stale,
    visibleCount: all.filter((e) => e.checked).length,
    totalCount: all.length,
  };
}

function sameKind(a, b) {
  return isCustomColumnId(a) === isCustomColumnId(b);
}

/** Show/hide a column. Hidden columns keep their slot (J2); unknown custom ids are appended. */
export function toggleColumn(config, id) {
  if (id === LOCKED_COLUMN_ID || !isKnownId(id)) return config;
  const present = config.columns.some((c) => c.id === id);
  if (!present) {
    if (config.columns.length >= MAX_COLUMN_ENTRIES) return config;
    return { ...config, columns: [...config.columns, { id }] };
  }
  const hidden = config.hidden.includes(id)
    ? config.hidden.filter((h) => h !== id)
    : [...config.hidden, id];
  return { ...config, hidden };
}

/** Move a column one step among entries of the same kind; the locked column never moves. */
export function moveColumn(config, id, delta) {
  if (id === LOCKED_COLUMN_ID || (delta !== -1 && delta !== 1)) return config;
  const index = config.columns.findIndex((c) => c.id === id);
  if (index < 0) return config;

  let target = index + delta;
  while (target >= 0 && target < config.columns.length) {
    const candidate = config.columns[target].id;
    if (candidate === LOCKED_COLUMN_ID) return config;
    if (sameKind(candidate, id)) break;
    target += delta;
  }
  if (target < 0 || target >= config.columns.length) return config;

  const columns = [...config.columns];
  [columns[index], columns[target]] = [columns[target], columns[index]];
  return { ...config, columns };
}

/** Drop a (stale) id from the config entirely. */
export function removeColumn(config, id) {
  if (id === LOCKED_COLUMN_ID) return config;
  return {
    ...config,
    columns: config.columns.filter((c) => c.id !== id),
    hidden: config.hidden.filter((h) => h !== id),
  };
}
