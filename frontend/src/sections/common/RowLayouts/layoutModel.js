// Row layouts (W9-4, DS-05; notify/DESIGN.md §5.1 D26): spec v1 of a saved
// Row-tab layout, its validation sentences and the pure edits the
// Customize panel makes. A layout is an r3 saved view (surface `rowLayout`,
// scope = dataset id, `state.layout` = this spec). Pure: no React, no I/O.
//
//   {v: 1, row_kind: "row" | "conversation" | "task", columns: 1 | 2,
//    fields: [{column_id, column_name, view: <VIEW_MODES value> | "custom",
//              side: "left" | "right", hidden, label?, collapsed?}],
//    controls: [{id, label, kind: "set_field" | "set_status" | "add_tag",
//                column_id?, value?, status?, reason?, tag?}],
//    renderer?: {html, height, author?}}   author: who wrote someone else's code
import { MODE_LABEL, VIEW_MODES } from "src/sections/common/Values/viewModes";
import { validateControls } from "./controlOps";

// The buttons' data lives in controlOps.js; re-exported for one import path.
export {
  CONTROL_KIND_LABEL,
  CONTROL_OP,
  controlKinds,
  controlOp,
  controlsDisabledReason,
  validateControls,
} from "./controlOps";

export const LAYOUT_VERSION = 1;
export const LAYOUT_SURFACE = "rowLayout";
export const ROW_KINDS = Object.freeze(["row", "conversation", "task"]);
export const SIDES = Object.freeze(["left", "right"]);
export const CUSTOM_VIEW = "custom";
/** W9-SCOPE §6 B4: the renderer's HTML is at most 64 KB. */
export const RENDERER_LIMIT_BYTES = 64 * 1024;
/** UI bounds of the renderer frame's height (px). */
export const RENDERER_HEIGHT = Object.freeze({
  min: 40,
  max: 2000,
  start: 240,
});

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const quote = (s) => `“${s}”`;

/** Dataset kind (`rows` / `conversations` / `task_suite`) → row kind. */
export function rowKindOf(datasetKind) {
  if (datasetKind === "conversations") return "conversation";
  if (datasetKind === "task_suite") return "task";
  return "row";
}

/** Every view a field can take: the ValueViewer modes, then `custom`. */
export function viewOptions(modes = VIEW_MODES) {
  return [
    ...modes.map((m) => ({ value: m, label: MODE_LABEL[m] ?? m })),
    { value: CUSTOM_VIEW, label: "Custom" },
  ];
}

/**
 * The Row tab's columns as layout columns, in the default view's order:
 * Input, Expected, Metadata, No role (user columns, editable), then the
 * generated outputs (read only).
 * @param {object} model  RowView's row model (rowModel.withEdits)
 */
export function layoutColumns(model) {
  const user = ["input", "expected", "metadata", "noRole"].flatMap((section) =>
    (model?.[section] ?? []).map((f) => ({ f, section, editable: true })),
  );
  const outputs = (model?.outputs ?? []).map((f) => ({
    f,
    section: "outputs",
    editable: false,
  }));
  return [...user, ...outputs]
    .filter(({ f }) => f?.column?.id && f?.column?.name)
    .map(({ f, section, editable }) => ({
      id: f.column.id,
      name: f.column.name,
      role: f.column.role ?? null,
      data_type: f.column.data_type ?? "text",
      origin_type: f.column.origin_type ?? null,
      section,
      editable,
    }));
}

/** A field of a column, as the default view shows it. */
export function fieldOf(column) {
  return {
    column_id: column.id,
    column_name: column.name,
    view: "auto",
    side: ["expected", "outputs"].includes(column.section) ? "right" : "left",
    hidden: false,
  };
}

/** The layout the Customize panel starts from: today's Row tab order. */
export function defaultLayout(columns = [], rowKind = "row") {
  return {
    v: LAYOUT_VERSION,
    row_kind: ROW_KINDS.includes(rowKind) ? rowKind : "row",
    columns: 1,
    fields: columns.map(fieldOf),
    controls: [],
  };
}

/** UTF-8 size of a string in bytes. */
export function byteSize(text) {
  const s = String(text ?? "");
  if (typeof TextEncoder !== "undefined")
    return new TextEncoder().encode(s).length;
  return unescape(encodeURIComponent(s)).length;
}

/** `12 KB` (rounded up; never `0 KB` for a non-empty text). */
export const kb = (bytes) => `${Math.ceil(bytes / 1024)} KB`;

/** A column by name: exact first, then ignoring case. */
export const columnByName = (columns, name) =>
  columns.find((c) => c.name === name) ??
  columns.find(
    (c) => String(c.name).toLowerCase() === String(name ?? "").toLowerCase(),
  ) ??
  null;

/** The column a field points at: by `column_id`, else by `column_name`. */
export const findColumn = (columns, field) =>
  columns.find((c) => c.id === field?.column_id) ??
  (field?.column_name != null
    ? columnByName(columns, field.column_name)
    : null);

/**
 * Validation sentences for a layout against the dataset's columns (empty
 * when it is valid). `opts.modes` = the ValueViewer modes; `opts.kinds` =
 * the control kinds this branch can apply; `opts.statuses` = the row
 * status labels a status button may set.
 */
export function validateLayout(spec, columns = [], opts = {}) {
  const modes = opts.modes ?? VIEW_MODES;
  const kinds = opts.kinds ?? ["set_field"];
  if (!isObj(spec)) return ["This layout can't be read."];
  const out = [];
  if (spec.v !== LAYOUT_VERSION)
    out.push(`This layout is format v${spec.v ?? "?"}; this page reads v1.`);
  if (!ROW_KINDS.includes(spec.row_kind))
    out.push(
      `Row kind ${quote(spec.row_kind)} isn't one of ${ROW_KINDS.join(", ")}.`,
    );
  if (spec.columns !== 1 && spec.columns !== 2)
    out.push("A layout has one or two columns.");
  const fields = Array.isArray(spec.fields) ? spec.fields : [];
  if (!Array.isArray(spec.fields)) out.push("This layout has no field list.");
  const seen = new Set();
  const hasRenderer = isObj(spec.renderer) && Boolean(spec.renderer.html);
  fields.forEach((f) => {
    const name = f?.column_name ?? f?.column_id ?? "?";
    const col = findColumn(columns, f ?? {});
    if (!col) out.push(`${quote(name)} isn't a column of this dataset.`);
    const key = col?.id ?? name;
    if (seen.has(key)) out.push(`${quote(name)} is in the layout twice.`);
    seen.add(key);
    if (f?.view !== CUSTOM_VIEW && !modes.includes(f?.view))
      out.push(`${quote(name)} can't be shown as ${quote(f?.view)}.`);
    if (f?.view === CUSTOM_VIEW && !hasRenderer)
      out.push(
        `${quote(name)} is set to Custom, but the layout has no custom renderer.`,
      );
    if (!SIDES.includes(f?.side))
      out.push(`${quote(name)} needs a side: left or right.`);
  });
  if (fields.length && fields.every((f) => f?.hidden) && !hasRenderer)
    out.push("Every field is hidden; show at least one.");
  out.push(
    ...validateControls(spec.controls ?? [], columns, kinds, opts.statuses),
  );
  if (spec.renderer != null) out.push(...validateRenderer(spec.renderer));
  return out;
}

/** Sentences for the custom renderer. */
export function validateRenderer(renderer) {
  if (!isObj(renderer)) return ["The custom renderer can't be read."];
  const out = [];
  const bytes = byteSize(renderer.html);
  if (bytes > RENDERER_LIMIT_BYTES)
    out.push(
      `The renderer is ${kb(bytes)}; the limit is ${kb(RENDERER_LIMIT_BYTES)}.`,
    );
  const h = Number(renderer.height);
  if (!Number.isFinite(h) || h < RENDERER_HEIGHT.min || h > RENDERER_HEIGHT.max)
    out.push(
      `The renderer height must be ${RENDERER_HEIGHT.min}–${RENDERER_HEIGHT.max} px.`,
    );
  return out;
}

/** A frame height asked for by the renderer, kept in the UI bounds. */
export function clampHeight(px) {
  const n = Math.round(Number(px));
  if (!Number.isFinite(n)) return RENDERER_HEIGHT.start;
  return Math.min(RENDERER_HEIGHT.max, Math.max(RENDERER_HEIGHT.min, n));
}

/**
 * Where the matched fields go: `{left, right, hidden}`. With one column
 * every shown field is on the left. `fields` are matchLayout's resolved
 * fields (they carry `column`).
 */
export function splitSides(spec, fields) {
  const two = spec?.columns === 2;
  const left = [];
  const right = [];
  const hidden = [];
  fields.forEach((f) => {
    if (f.hidden) hidden.push(f);
    else if (two && f.side === "right") right.push(f);
    else left.push(f);
  });
  return { left, right, hidden };
}

// ------------------------------------------------------------ edits (pure)

/** Move the field at `from` to `to` (array positions). */
export function moveField(spec, from, to) {
  const fields = [...spec.fields];
  if (from < 0 || from >= fields.length || to < 0 || to >= fields.length)
    return spec;
  const [f] = fields.splice(from, 1);
  fields.splice(to, 0, f);
  return { ...spec, fields };
}

/** Merge `patch` into the field of `columnId`. */
export function patchField(spec, columnId, patch) {
  return {
    ...spec,
    fields: spec.fields.map((f) =>
      f.column_id === columnId ? { ...f, ...patch } : f,
    ),
  };
}

/** A fresh control id (`ctl_3`), unique in the layout. */
export function nextControlId(controls = []) {
  const n = controls.reduce((m, c) => {
    const k = Number(String(c?.id ?? "").replace(/^ctl_/, ""));
    return Number.isFinite(k) && k > m ? k : m;
  }, 0);
  return `ctl_${n + 1}`;
}

/** Replace, add (no id match) or remove (`null`) one control. */
export function putControl(spec, id, control) {
  const controls = spec.controls ?? [];
  if (control == null)
    return { ...spec, controls: controls.filter((c) => c.id !== id) };
  return controls.some((c) => c.id === id)
    ? { ...spec, controls: controls.map((c) => (c.id === id ? control : c)) }
    : { ...spec, controls: [...controls, control] };
}

/** Set or remove (`null`) the custom renderer; Custom fields go back to Auto. */
export function setRenderer(spec, renderer) {
  if (renderer) return { ...spec, renderer };
  const { renderer: _gone, ...rest } = spec;
  return {
    ...rest,
    fields: spec.fields.map((f) =>
      f.view === CUSTOM_VIEW ? { ...f, view: "auto" } : f,
    ),
  };
}
