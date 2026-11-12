// A results row's input as a person reads it. A native run's row (prompt or
// agent graph) stores its input as the snapshot envelope
// `{values: {<dataset column id>: value}}` (native_run_binding), so the
// Input cell showed `{"<uuid>": "What is…"}`. This unwraps the envelope and
// names each value by its dataset column. Pure.

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
