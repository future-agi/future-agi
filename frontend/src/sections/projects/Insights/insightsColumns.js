import { UNITS } from "src/sections/projects/observePopulation";
import { humanizeKey } from "./insightsFormat";

// Columns of the categories table. "Category" is always shown; the rest can
// be switched on and off from the Columns menu, and the choice is kept per
// report in this browser.

const PREFIX = "insights-columns:";
export const ATTRIBUTE_PREFIX = "attr:";

const BASE_COLUMNS = [
  {
    key: "share",
    label: "Share",
    width: 140,
    defaultOn: true,
    help: "Share of the analysed items in this category, and how many. Hover the bar for its 95% confidence interval.",
  },
  {
    key: "users",
    label: "Users affected",
    align: "right",
    width: 116,
    defaultOn: true,
    help: "Distinct users with at least one item in this category.",
  },
  {
    key: "notable",
    label: "Stands out",
    width: 200,
    defaultOn: true,
    help: "Where this category differs from the rest of the report, after correcting for every comparison made.",
  },
  {
    key: "change",
    label: "Change",
    align: "right",
    width: 88,
    defaultOn: true,
    help: "Change in share since the previous run, in percentage points.",
  },
  { key: "count", label: "Count", align: "right", width: 80 },
  { key: "sessions", label: "Sessions", align: "right", width: 88 },
  {
    key: "failure",
    label: "Failure %",
    align: "right",
    width: 88,
    help: "Share with an agent mistake.",
  },
  {
    key: "unresolved",
    label: "Unresolved %",
    align: "right",
    width: 104,
    help: "Share that ended unresolved or abandoned.",
  },
  {
    key: "sentiment",
    label: "Sentiment",
    align: "right",
    width: 88,
    help: "Average user sentiment, 1 (very negative) to 5 (very positive).",
  },
  {
    key: "latency",
    label: "Median latency",
    align: "right",
    width: 112,
  },
  {
    key: "cost",
    label: "Cost",
    align: "right",
    width: 80,
    help: "Average cost.",
  },
  {
    key: "errors",
    label: "Error %",
    align: "right",
    width: 80,
    help: "Share that hit an error.",
  },
];

/**
 * The columns this report can show, in menu order:
 *   unit      the report's unit; "Count" reads as its plural, "Sessions" only
 *             exists for traces and "Users affected" not for users
 *   attributes the report's attributes, one column each
 *   hasChanges whether the run has a previous run to compare with
 */
export function availableColumns({
  unit,
  attributes = [],
  hasChanges = false,
} = {}) {
  const plural = (UNITS[unit] || UNITS.traces).title;
  const columns = BASE_COLUMNS.filter((column) => {
    if (column.key === "sessions") return unit === "traces";
    if (column.key === "users") return unit !== "users";
    if (column.key === "change") return hasChanges;
    return true;
  }).map((column) =>
    column.key === "count" ? { ...column, label: plural } : column,
  );
  (attributes || []).forEach((attribute) => {
    if (!attribute?.key) return;
    columns.push({
      key: `${ATTRIBUTE_PREFIX}${attribute.key}`,
      label: humanizeKey(attribute.key),
      align: "right",
      width: 112,
      attribute: attribute.key,
      attributeType: attribute.type,
      help: attribute.description || undefined,
    });
  });
  return columns;
}

export const defaultColumnKeys = (columns) =>
  columns.filter((column) => column.defaultOn).map((column) => column.key);

// A saved choice is {on: [...], off: [...]}: what the user switched on or
// off. Anything they never touched follows its default, so a column that
// only appears on a later run (Change) still shows up.
const cleanKeys = (keys) =>
  Array.isArray(keys) ? keys.filter((key) => typeof key === "string") : [];

/** Saved choice for a report: {on, off}, or null when nothing is saved. */
export function readColumnPrefs(reportId) {
  if (!reportId) return null;
  try {
    const raw = globalThis.localStorage?.getItem(PREFIX + reportId);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") return null;
    return { on: cleanKeys(parsed.on), off: cleanKeys(parsed.off) };
  } catch {
    return null;
  }
}

export function writeColumnPrefs(reportId, prefs) {
  if (!reportId) return;
  try {
    globalThis.localStorage?.setItem(
      PREFIX + reportId,
      JSON.stringify({ on: cleanKeys(prefs?.on), off: cleanKeys(prefs?.off) }),
    );
  } catch {
    // Storage full or blocked: the choice lasts until the page reloads.
  }
}

const isOn = (column, prefs) => {
  if (prefs?.on?.includes(column.key)) return true;
  if (prefs?.off?.includes(column.key)) return false;
  return Boolean(column.defaultOn);
};

/** The columns to render, in the menu's order. */
export const visibleColumns = (columns, prefs) =>
  columns.filter((column) => isOn(column, prefs));

/** The choice after switching one column. */
export function toggleColumn(columns, prefs, key) {
  const column = columns.find((c) => c.key === key);
  const on = new Set(prefs?.on || []);
  const off = new Set(prefs?.off || []);
  if (column && isOn(column, prefs)) {
    on.delete(key);
    off.add(key);
  } else {
    off.delete(key);
    on.add(key);
  }
  return { on: [...on], off: [...off] };
}
