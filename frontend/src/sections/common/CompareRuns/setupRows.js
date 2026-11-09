// Task compare's Setup rows (W5-6 B6, CP-07; review M3). Pure. What differs
// between lanes is decided only by the one config diff: every lane against
// the first lane through configRows (configDiff(a, b, {all: true})), one
// row per change, exactly the rows Explore › Config lists for that pair.
// The lane run-info rows (Model, Framework, Version, Tools, MCP; LaneHeader
// runInfoRows) and the B6 extras (Temperature, Prompt version, Dataset
// version, Evaluations; setupExtras) are the unchanged settings: shown with
// `Differences only` off, never counted, and left out when a change row
// already shows that setting.
import { AXIS_NOUNS } from "src/sections/common/Runs/configDiff";
import {
  CONFIG_GROUPS,
  changeSignature,
  configRows,
  listSideText,
  setupExtras,
} from "./configRows";

const ARROW = " → ";

/** The changed-setting axes each unchanged row stands for. */
const ROW_AXES = Object.freeze({
  Model: (c) => c.axis === "model",
  Framework: (c) => c.axis === "kind" || c.axis === "framework",
  Version: (c) =>
    c.axis === "prompt" || (c.axis === "agent" && !c.detail?.field),
  Tools: (c) => c.axis === "tools",
  MCP: (c) => c.axis === "mcp",
  Temperature: (c) => c.axis === "params" && c.detail?.param === "temperature",
  "Prompt version": (c) => c.axis === "prompt",
  "Dataset version": (c) => c.axis === "dataset" && !c.detail?.field,
  Evaluations: (c) => c.axis === "evals",
});

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const sided = (c) => c.detail?.from !== undefined || c.detail?.to !== undefined;
const side = (v) =>
  v == null || v === "none"
    ? "—"
    : Array.isArray(v)
      ? listSideText(v)
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);

/**
 * A change row's setting name, Title Case, from the chip's own words:
 * `max tokens 400 → 800` → `Max tokens`; a version chip names its setting
 * (`Prompt version`, `Dataset version`, `Evaluations`).
 */
export function settingName(chip) {
  if (chip.axis === "prompt") return "Prompt version";
  if (chip.axis === "evals") return "Evaluations";
  if (chip.axis === "dataset" && !chip.detail?.field) return "Dataset version";
  if (chip.axis === "model") return "Model";
  if (sided(chip)) {
    // The words before `<from> → `; an unset side reads `none`, `default`
    // or `all rows` in the chip.
    const label = String(chip.label ?? chip.text ?? "");
    const at = [side(chip.detail.from), "none", "default", "all rows"]
      .map((from) => label.indexOf(` ${from}${ARROW}`))
      .find((i) => i > 0);
    if (at) return cap(label.slice(0, at));
  }
  if (chip.detail?.field)
    return cap(String(chip.detail.field).replace(/_/g, " "));
  return cap(AXIS_NOUNS[chip.axis] ?? chip.axis);
}

/** One lane's value on a change row (`gpt-4o`, `groundedness v3`, `+search_kb`). */
function sideValue(chip, which) {
  if (!sided(chip)) return which === "to" ? chip.text ?? chip.label : null;
  const v = side(chip.detail[which]);
  return chip.axis === "evals" && chip.detail?.eval
    ? `${chip.detail.eval} ${v}`
    : v;
}

/** Which setting a change is about, so lanes changing it line up. */
const changeKey = (chip) =>
  [
    chip.axis,
    chip.detail?.param ?? chip.detail?.field ?? chip.detail?.eval ?? "",
    chip.detail?.server ?? "",
    sided(chip) ? "" : chip.text ?? chip.label,
  ].join("|");

/**
 * @param {Array<{run: object|null, info: Array<[string, string]>|null}>} cols
 *   one per lane, the baseline lane first; `info` = the lane's run-info rows
 *   (`runInfoRows`), null for a blind lane (nothing of it is shown or
 *   compared)
 * @returns {{count: number, rows: Array<{key: string, name: string,
 *   values: Array<string|null>, differs: boolean[], changed: boolean}>,
 *   notCompared: number[]}}  `count` = the change rows; for two lanes it
 *   equals `configRows(a, b).count`. `notCompared` = lanes whose run or the
 *   baseline's recorded no settings (configDiff cannot compare them).
 */
export function setupRows(cols) {
  const lanes = cols ?? [];
  const base = lanes[0];
  const hidden = (c) => !c || c.info == null;
  const changes = new Map();
  const notCompared = [];
  lanes.forEach((c, i) => {
    if (i === 0 || hidden(c) || hidden(base)) return;
    if (!base.run?.config || !c.run?.config) {
      notCompared.push(i);
      return;
    }
    const seen = new Map();
    const sigs = new Set();
    configRows(base.run, c.run).chips.forEach((chip) => {
      // L7c g6: one row per setting (configRows' rule): a repeat of the
      // same change (one tools chip per tool) adds no row.
      const sig = changeSignature(chip);
      if (sig && sigs.has(sig)) return;
      if (sig) sigs.add(sig);
      // A second change of the same kind in one pair keeps its own row.
      const k0 = changeKey(chip);
      const nth = seen.get(k0) ?? 0;
      seen.set(k0, nth + 1);
      const key = `${k0}#${nth}`;
      if (!changes.has(key))
        changes.set(key, { key, chip, name: settingName(chip), by: new Map() });
      changes.get(key).by.set(i, chip);
    });
  });
  const changeRows = [...changes.values()].map((ch) => {
    const from = sideValue(ch.chip, "from");
    const values = lanes.map((c, i) => {
      if (hidden(c)) return null;
      if (i === 0) return from;
      const own = ch.by.get(i);
      return own ? sideValue(own, "to") : from;
    });
    return {
      key: ch.key,
      name: ch.name,
      values,
      differs: lanes.map((_, i) => ch.by.has(i)),
      changed: true,
    };
  });
  const allChips = [...changes.values()].flatMap((ch) => [...ch.by.values()]);
  const shown = (name) => !allChips.some((chip) => ROW_AXES[name]?.(chip));
  const settings = lanes.map((c) =>
    hidden(c)
      ? null
      : {
          ...Object.fromEntries(c.info ?? []),
          ...Object.fromEntries(
            Object.entries(setupExtras(c.run)).filter(([, v]) => v != null),
          ),
        },
  );
  // `Prompt version` only adds a row when it says more than `Version`.
  const sameAsVersion = settings.every(
    (s) => !s || (s["Prompt version"] ?? null) === (s.Version ?? null),
  );
  const unchangedRows = Object.keys(ROW_AXES)
    .filter((name) => settings.some((s) => s?.[name]))
    .filter((name) => name !== "Prompt version" || !sameAsVersion)
    .filter(shown)
    .map((name) => ({
      key: `setting|${name}`,
      name,
      values: settings.map((s) => (s ? s[name] ?? null : null)),
      differs: lanes.map(() => false),
      changed: false,
    }));
  // One order for both: a change row sits where the setting it changes
  // sits (Model, Framework, …), the others after them in Explore › Config's
  // group order.
  const names = Object.keys(ROW_AXES);
  const groupAt = (axis) =>
    CONFIG_GROUPS.findIndex(([, axes]) => axes.includes(axis));
  const place = (row) => {
    if (!row.changed) return names.indexOf(row.name);
    const chip = changes.get(row.key).chip;
    const at = names.findIndex((name) => ROW_AXES[name](chip));
    return at >= 0 ? at : names.length + Math.max(0, groupAt(chip.axis));
  };
  const rows = [...unchangedRows, ...changeRows]
    .map((row, i) => ({ row, at: place(row), i }))
    .sort((a, b) => a.at - b.at || a.i - b.i)
    .map((x) => x.row);
  return { count: changeRows.length, rows, notCompared };
}
