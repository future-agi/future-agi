// HO results R1 (W5 integration A-H1, A-H4): a dataset's custom columns as
// results grid column groups. The definitions come from the R3 summary
// (`custom_columns`) and every value from the R3 row itself (`custom` on the
// row for row columns, on each result for run columns), which the mock
// computes with the one value function (w5CustomColumns.customColumnValues).
// Pure: AG Grid column defs, catalog entries and the group menu items.

export const CUSTOM_PREFIX = "custom:";
export const CUSTOM_GROUP = "custom";
const WIDTH = { run: 128, row: 140 };
const EMPTY = "—";

/** The catalog / layout key of a custom column (`custom:<id>`). */
export const customKeyOf = (def) => `${CUSTOM_PREFIX}${def.id}`;
export const isCustomKey = (key) => String(key ?? "").startsWith(CUSTOM_PREFIX);

/** The summary's custom column definitions (oldest first, as R3 sends them). */
export const customColumnsOf = (summary) =>
  (summary?.custom_columns ?? []).filter((d) => d && d.id && d.name);

/** The value of `def` on a wire row (`runId` null for a row column). */
export function customValueOf(row, def, runId) {
  if (def.applies_to === "row") return row?.custom?.[def.id] ?? null;
  return row?.results?.[runId]?.custom?.[def.id] ?? null;
}

/** Why the cell is empty, when the server said (`Unknown column “x”.`). */
export function customErrorOf(row, def, runId) {
  if (def.applies_to === "row") return row?.custom_errors?.[def.id] ?? null;
  return row?.results?.[runId]?.custom_errors?.[def.id] ?? null;
}

const trimNumber = (n) => {
  if (Number.isInteger(n)) return n.toLocaleString("en-US");
  const abs = Math.abs(n);
  const digits = abs >= 100 ? 1 : abs >= 1 ? 2 : 4;
  return Number(n.toFixed(digits)).toLocaleString("en-US", {
    maximumFractionDigits: digits,
  });
};

/** A cell's text: numbers trimmed, booleans as words, lists and objects as JSON. */
export function formatCustomValue(v) {
  if (v === null || v === undefined || v === "") return EMPTY;
  if (typeof v === "number") return Number.isFinite(v) ? trimNumber(v) : EMPTY;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

/** Catalog entries for useColumnLayout (one per custom column). */
export const customCatalogEntries = (summary) =>
  customColumnsOf(summary).map((def) => ({
    id: customKeyOf(def),
    label: def.name,
    group: CUSTOM_GROUP,
    description:
      def.kind === "path"
        ? `From ${def.path}`
        : `= ${String(def.expr ?? "").slice(0, 80)}`,
  }));

/** The sort string of a custom column (`custom:<id>:<run_id | row>:<dir>`). */
export const customSortOf = (def, runId, dir) =>
  `${CUSTOM_PREFIX}${def.id}:${def.applies_to === "row" ? "row" : runId}:${dir}`;

const RIGHT = { textAlign: "right", fontVariantNumeric: "tabular-nums" };

const leaf = ({ def, runId, colId, headerName, extra }) => ({
  colId,
  headerName,
  width: def.applies_to === "row" ? WIDTH.row : WIDTH.run,
  ...extra,
  valueGetter: (p) => customValueOf(p.data, def, runId),
  valueFormatter: (p) => formatCustomValue(p.value),
  tooltipValueGetter: (p) => {
    const err = customErrorOf(p.data, def, runId);
    if (err) return err;
    const v = p.value;
    return v != null && typeof v === "object"
      ? JSON.stringify(v, null, 1)
      : null;
  },
  cellStyle: def.type === "number" ? RIGHT : undefined,
  sortable: false,
});

/**
 * AG Grid column groups for the summary's custom columns: a run column is a
 * group with one child per run (each with its run header, like Cost); a row
 * column is a group with one `Value` child. `entry(key, def)` receives each
 * (buildResultColumns' `at(key).push`).
 * @param {object} o {summary, runIds, runsById, colors, baselineId, single,
 *   groupMenu(group) → items, isHidden(key)}
 */
export function pushCustomColumns(entry, o) {
  const {
    summary,
    runIds = [],
    runsById = {},
    colors = {},
    baselineId = null,
    single = runIds.length < 2,
    groupMenu,
    isHidden = () => false,
  } = o;
  customColumnsOf(summary).forEach((def) => {
    const key = customKeyOf(def);
    if (isHidden(key)) return;
    const group = {
      key,
      kind: CUSTOM_GROUP,
      custom: def,
      runIds,
      runLabels: Object.fromEntries(
        runIds.map((id) => [id, runsById[id]?.label ?? null]),
      ),
    };
    const children =
      def.applies_to === "row"
        ? [
            leaf({
              def,
              runId: null,
              colId: `${key}|row`,
              headerName: "Value",
            }),
          ]
        : runIds.map((runId) =>
            leaf({
              def,
              runId,
              colId: `${key}|${runId}`,
              headerName: runsById[runId]?.label ?? def.name,
              extra: {
                headerComponent: "resultRunHeader",
                headerComponentParams: {
                  run: runsById[runId],
                  color: colors[runId],
                  isBaseline: runId === baselineId && !single,
                },
              },
            }),
          );
    entry(key, {
      groupId: key,
      marryChildren: true,
      headerName: def.name,
      headerGroupComponent: "resultGroupHeader",
      headerGroupComponentParams: {
        resultGroup: {
          key,
          kind: CUSTOM_GROUP,
          name: def.name,
          menuItems: groupMenu?.(group) ?? [],
        },
      },
      children,
    });
  });
}

/**
 * The ⋮ items of a custom column group (before the layout items): sort by it
 * on the first candidate (or the only run) both ways, edit it, add another.
 * `o.sortBy(sort)`, `o.openColumn(initial | null)`, `o.firstCandidate`,
 * `o.readOnly`.
 */
export function customGroupMenuItems(group, o = {}) {
  const def = group.custom;
  if (!def) return [];
  const runId =
    def.applies_to === "row"
      ? null
      : o.firstCandidate ?? group.runIds?.[0] ?? null;
  const on =
    def.applies_to === "row" || !runId
      ? ""
      : ` · ${group.runLabels?.[runId] ?? "this run"}`;
  const canSort = def.applies_to === "row" || Boolean(runId);
  const items = [
    {
      label: `Sort ascending${on}`,
      icon: "material-symbols:arrow-upward-rounded",
      disabled: !canSort || !o.sortBy,
      onClick: () => o.sortBy?.(customSortOf(def, runId, "asc")),
    },
    {
      label: `Sort descending${on}`,
      icon: "material-symbols:arrow-downward-rounded",
      disabled: !canSort || !o.sortBy,
      onClick: () => o.sortBy?.(customSortOf(def, runId, "desc")),
    },
  ];
  if (o.openColumn) {
    items.push({
      label: "Edit column…",
      icon: "mdi:pencil-outline",
      divider: true,
      disabled: Boolean(o.readOnly),
      secondary: o.readOnly
        ? "Viewers can look at columns but not change them."
        : undefined,
      onClick: () => o.openColumn(def),
    });
  }
  return items;
}

/** `+ Add column…` (A-H4) for any group's ⋮ menu, or nothing without a door. */
export function addColumnItem(o = {}) {
  if (!o.openColumn) return [];
  return [
    {
      label: "Add column…",
      icon: "mdi:table-column-plus-after",
      divider: true,
      disabled: Boolean(o.readOnly),
      secondary: o.readOnly
        ? "Viewers can look at columns but not change them."
        : undefined,
      onClick: () => o.openColumn(null),
    },
  ];
}

/** `custom:<id>:<run | row>:<dir>` → `{colId, target, dir}`, or null (same rule as the mock). */
export function parseCustomSortString(sort) {
  const parts = String(sort ?? "").split(":");
  if (parts[0] !== "custom" || parts.length < 3) return null;
  const hasDir = ["asc", "desc"].includes(parts.at(-1));
  const rest = hasDir ? parts.slice(1, -1) : parts.slice(1);
  if (rest.length < 2) return null;
  return {
    colId: rest[0],
    target: rest.slice(1).join(":"),
    dir: hasDir ? parts.at(-1) : "asc",
  };
}

/** `Sorted by intent_out ↑ · #2B` (the row's value: no run label). */
export function customSortLabel(parsed, def, runs = []) {
  if (!parsed) return "";
  const arrow = parsed.dir === "desc" ? "↓" : "↑";
  const name = def?.name ?? "a custom column";
  const run =
    parsed.target === "row"
      ? null
      : runs.find((r) => (r.run_id ?? r.id) === parsed.target)?.label ?? null;
  return `Sorted by ${name} ${arrow}${run ? ` · ${run}` : ""}`;
}

/** The narrow toolbar's chip text (`out_len · #2B`); its icon is the arrow. */
export function customSortShortLabel(parsed, def, runs = []) {
  if (!parsed) return "";
  const name = def?.name ?? "a custom column";
  const run =
    parsed.target === "row"
      ? null
      : runs.find((r) => (r.run_id ?? r.id) === parsed.target)?.label ?? null;
  return `${name}${run ? ` · ${run}` : ""}`;
}
