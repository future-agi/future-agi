// A results row's input as a person reads it. A native run's row (prompt or
// agent graph) stores its input as the snapshot envelope
// `{values: {<dataset column id>: value}}` (native_run_binding), so the
// Input cell showed `{"<uuid>": "What is…"}`. This unwraps the envelope and
// names each value by its dataset column. Pure.
import { columnMediaType } from "src/sections/common/Media/mediaModel";

const isPlainObject = (v) =>
  v != null && typeof v === "object" && !Array.isArray(v);

/**
 * `{id: name}` from the dataset's columns (`[{id, name}]`), skipping
 * columns without both.
 * @param {Array<{id?: string, name?: string}>} columns
 * @returns {Record<string, string>}
 */
export function columnNamesById(columns) {
  const out = {};
  (Array.isArray(columns) ? columns : []).forEach((c) => {
    if (c?.id != null && typeof c?.name === "string" && c.name.trim())
      out[String(c.id)] = c.name;
  });
  return out;
}

/**
 * `{input name: data type}` for the dataset's media columns, under the names
 * a results row keys them by. The columns are read in the order given (the
 * dataset's column order, as the columns read returns them): the first
 * column with a name takes that name, and a later column whose name is
 * already taken takes `name (id)`, as the results read and displayInputs
 * name them. `name (id)` is mapped for every media column too, because rows
 * named here follow the stored values' key order. Other columns are left
 * out, so their inputs read as before. An uploaded document's URL has no
 * file extension: its column type is what says it is a document.
 * @param {Array<{id?: string, name?: string, data_type?: string}>} columns
 * @returns {Record<string, string>}
 */
export function columnTypesByName(columns) {
  const taken = new Set();
  const out = {};
  (Array.isArray(columns) ? columns : []).forEach((c) => {
    if (typeof c?.name !== "string" || !c.name.trim()) return;
    const suffixed = c.id != null ? `${c.name} (${c.id})` : null;
    const label = taken.has(c.name) ? suffixed : c.name;
    if (label == null) return;
    taken.add(label);
    if (!columnMediaType(c.data_type)) return;
    out[label] = c.data_type;
    if (suffixed != null) out[suffixed] = c.data_type;
  });
  return out;
}

/**
 * The row's inputs keyed by column name. Only the native envelope (an
 * object whose one key is `values`, holding an object) is unwrapped and
 * re-keyed; an id with no known name keeps its id. Anything else (inputs
 * already keyed by field name, a string, null) comes back unchanged.
 * @param {unknown} inputs  a results row's `inputs`
 * @param {Record<string, string>} [namesById]
 */
export function displayInputs(inputs, namesById) {
  if (!isPlainObject(inputs)) return inputs;
  const keys = Object.keys(inputs);
  if (keys.length !== 1 || keys[0] !== "values") return inputs;
  const values = inputs.values;
  if (!isPlainObject(values)) return inputs;
  const names = namesById ?? {};
  const out = {};
  Object.entries(values).forEach(([id, value]) => {
    let name = names[id] ?? id;
    // Two columns with one name keep both values.
    if (Object.hasOwn(out, name)) name = `${name} (${id})`;
    out[name] = value;
  });
  return out;
}
