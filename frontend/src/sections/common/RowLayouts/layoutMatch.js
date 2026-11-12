// Row layouts (W9-4): which dataset column each layout field is. A field is
// matched by `column_id`, falling back to `column_name` (a layout copied
// from another dataset, or a column re-created under the same name). Pure.
import { CUSTOM_VIEW, columnByName, fieldOf, findColumn } from "./layoutModel";

const byName = columnByName;

/** The column a field points at, or null. */
export function columnOfField(field, columns = []) {
  if (!field) return null;
  return findColumn(columns, field);
}

/**
 * The layout's fields resolved against the dataset's columns.
 * - `fields`: the layout order, each `{...field, column}` (matched only)
 * - `missing`: layout fields with no column here
 * - `unplaced`: columns the layout does not mention (new since it was
 *   saved); they show under "Hidden fields" so nothing is ever lost
 */
export function matchLayout(spec, columns = []) {
  const used = new Set();
  const fields = [];
  const missing = [];
  (spec?.fields ?? []).forEach((f) => {
    const column = columnOfField(f, columns);
    if (!column || used.has(column.id)) {
      if (!column) missing.push(f);
      return;
    }
    used.add(column.id);
    fields.push({ ...f, column_id: column.id, column });
  });
  const unplaced = columns
    .filter((c) => !used.has(c.id))
    .map((c) => ({ ...fieldOf(c), hidden: true, unplaced: true, column: c }));
  return { fields, missing, unplaced };
}

/**
 * The line for layout fields whose column is gone (deleted or renamed since
 * the layout was saved), or null: `Not a column here any more: a, b. The
 * layout skips them.`
 */
export function missingLine(missing = []) {
  if (!missing.length) return null;
  const names = missing.map((f) => f?.column_name ?? f?.column_id ?? "?");
  return `Not a column here any more: ${names.join(", ")}. The layout skips ${
    names.length === 1 ? "it" : "them"
  }.`;
}

/**
 * Every column the Customize panel lists, in the layout's order, then the
 * columns the layout does not mention (hidden). Returns plain fields
 * (no `column`), with ids pointing at this dataset.
 */
export function editableFields(spec, columns = []) {
  const { fields, unplaced } = matchLayout(spec, columns);
  const strip = ({ column: _c, unplaced: _u, ...f }) => f;
  return [...fields.map(strip), ...unplaced.map(strip)];
}

/**
 * A layout copied to another dataset: fields and `set_field` buttons are
 * re-pointed by column name; what has no column there is left out.
 * @returns {{spec: object, matched: number, total: number,
 *   missing: string[], droppedControls: string[]}}
 */
export function copyLayoutTo(spec, targetColumns = []) {
  const fields = [];
  const missing = [];
  (spec?.fields ?? []).forEach((f) => {
    const column = byName(targetColumns, f.column_name);
    if (!column) {
      missing.push(f.column_name);
      return;
    }
    if (fields.some((x) => x.column_id === column.id)) return;
    fields.push({ ...f, column_id: column.id, column_name: column.name });
  });
  const droppedControls = [];
  const controls = (spec?.controls ?? []).filter((c) => {
    if (c.kind !== "set_field") return true;
    const from = (spec?.fields ?? []).find((f) => f.column_id === c.column_id);
    const column = byName(targetColumns, from?.column_name);
    if (!column || column.editable === false) {
      droppedControls.push(c.label);
      return false;
    }
    return true;
  });
  const repointed = controls.map((c) => {
    if (c.kind !== "set_field") return c;
    const from = (spec?.fields ?? []).find((f) => f.column_id === c.column_id);
    const column = byName(targetColumns, from?.column_name);
    return { ...c, column_id: column.id };
  });
  const out = { ...spec, fields, controls: repointed };
  // A field set to Custom keeps its view only while the renderer exists.
  if (!out.renderer)
    out.fields = out.fields.map((f) =>
      f.view === CUSTOM_VIEW ? { ...f, view: "auto" } : f,
    );
  return {
    spec: out,
    matched: fields.length,
    shown: fields.filter((f) => !f.hidden).length,
    total: (spec?.fields ?? []).length,
    missing,
    droppedControls,
  };
}

/** `Copied. 4 of 6 fields matched by name. Not in TS: a, b (left out of the copy).` */
export function copyResultLine(result, targetName) {
  const head = `${result.matched} of ${result.total} field${
    result.total === 1 ? "" : "s"
  } matched by name.`;
  const miss = result.missing.length
    ? ` Not in ${targetName}: ${result.missing.join(", ")} (left out of the copy).`
    : "";
  const ctl = result.droppedControls.length
    ? ` Buttons left out: ${result.droppedControls.join(", ")} (their column isn't editable there).`
    : "";
  return `${head}${miss}${ctl}`;
}

/** Target columns: user columns are editable, run outputs are not. */
export function targetColumns(userCols = [], allCols = []) {
  const user = new Set(userCols.map((c) => c.id));
  const seen = new Set();
  return [...userCols, ...allCols]
    .filter((c) => c?.id && !seen.has(c.id) && seen.add(c.id))
    .map((c) => ({ id: c.id, name: c.name, editable: user.has(c.id) }));
}

/** column id → `{value, original, edited, editable}` from RowView's model. */
export function valuesOf(model) {
  const out = {};
  ["input", "expected", "metadata", "noRole"].forEach((k) =>
    (model?.[k] ?? []).forEach((f) => {
      out[f.column.id] = {
        value: f.value,
        original: f.original,
        edited: Boolean(f.edited),
        editable: true,
      };
    }),
  );
  (model?.outputs ?? []).forEach((o) => {
    out[o.column.id] = {
      value: o.cell?.cell_value ?? "",
      edited: false,
      editable: false,
    };
  });
  return out;
}
