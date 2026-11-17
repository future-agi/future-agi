// Variable mapping for experiment runs (PRODUCT-SPEC §3.8 step 2). A run's
// variables (prompt `variable_names`, agent `global_variables`, HTTP body
// `{{vars}}`) map to dataset columns. Auto-map tries exact, then
// case-insensitive, then normalized names (snake / camel / spaces).
//
// A mapping value is a column id, or `<column_id>::$.path` for a JSON path.
// R2 (P6, L6): paths are FA Paths (faPath.js) — `{{conversation[-1].content}}`
// auto-maps to column `conversation` + path `[-1].content`, values resolve
// typed (negative indexes, `[*]`, quoted keys), coverage comes from the
// json-schema's typed `paths[]`.
import {
  INPUT_SYNONYMS,
  inputColumnFor,
} from "src/sections/common/RunSources/runOnRows";
import {
  formatPath,
  fromLegacyMapping,
  parsePath,
  resolvePath,
  toLegacyMapping,
} from "src/sections/common/Values/faPath";
import { mappedConversationValue } from "src/sections/common/RunSettings/conversationPaths";

export const JSON_PATH_SEPARATOR = "::";

/** `customerMessage`, `Customer Message`, `customer-message` -> `customer_message`. */
export function normalizeName(name) {
  return String(name ?? "")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

/**
 * Best column for one variable.
 * @param {string} variable
 * @param {{id: string, name: string}[]} columns
 * @returns {{columnId: string, how: "exact"|"case"|"normalized"}|null}
 */
export function autoMapVariable(variable, columns = []) {
  const exact = columns.find((c) => c.name === variable);
  if (exact) return { columnId: exact.id, how: "exact" };
  const lower = String(variable).toLowerCase();
  const ci = columns.find((c) => String(c.name).toLowerCase() === lower);
  if (ci) return { columnId: ci.id, how: "case" };
  const norm = normalizeName(variable);
  const nm = norm && columns.find((c) => normalizeName(c.name) === norm);
  if (nm) return { columnId: nm.id, how: "normalized" };
  // `conversation[-1].content` → column `conversation`, path `[-1].content`
  const parsed = parsePath(variable, { columns: columns.map((c) => c.name) });
  if (parsed.ok && parsed.column && parsed.steps.length) {
    const col = columns.find((c) => c.name === parsed.column);
    if (col)
      return {
        columnId: col.id,
        how: "path",
        value: toLegacyMapping(col.id, formatPath(null, parsed.steps)),
      };
  }
  // R4 (lane Ra, P6 · D24): by meaning: `user_message` → `customer_message`
  // (F's INPUT_SYNONYMS); a conversation's last customer turn.
  const byMeaning = inputColumnFor(variable, columns);
  if (byMeaning) {
    const col = columns.find((c) => c.name === byMeaning.column);
    if (col && byMeaning.how === "synonym")
      return { columnId: col.id, how: "meaning" };
    if (col && byMeaning.how === "last customer turn")
      return {
        columnId: col.id,
        how: "last customer turn",
        value: toLegacyMapping(
          col.id,
          formatPath(null, parsePath("[-1].content", {}).steps ?? []),
        ),
      };
  }
  return null;
}

/**
 * How a variable's current mapping was matched, for the chip next to it:
 * `meaning` (synonyms) when the column is the synonym pick and the names
 * differ, else null (pure).
 */
export function mappingHowOf(variable, value, columns = []) {
  if (isFixedMapping(value)) return null;
  const id = String(value ?? "").split(JSON_PATH_SEPARATOR)[0];
  const col = columns.find((c) => c.id === id);
  if (!col || normalizeName(col.name) === normalizeName(variable)) return null;
  const hit = autoMapVariable(variable, columns);
  if (
    hit?.columnId === col.id &&
    (hit.how === "meaning" || hit.how === "last customer turn")
  )
    return hit.how;
  return null;
}

/** The tooltip of `matched by meaning` (pure). */
export const meaningTooltip = (variable, column) =>
  INPUT_SYNONYMS.includes(String(variable).toLowerCase()) &&
  INPUT_SYNONYMS.includes(String(column).toLowerCase())
    ? `${variable} and ${column} both hold the customer's words`
    : `${variable} and ${column} mean the same input`;

// IT-R4 G1 P1: a variable with no column never blocks. Besides a column
// id, a mapping value is `{kind: "empty"}` (every row gets "") or
// `{kind: "constant", value}` (every row gets the same text).
export const EMPTY_MAPPING = Object.freeze({ kind: "empty" });
export const constantMapping = (value = "") => ({
  kind: "constant",
  value: String(value ?? ""),
});
export const isEmptyMapping = (v) =>
  Boolean(v) && typeof v === "object" && v.kind === "empty";
export const isConstantMapping = (v) =>
  Boolean(v) && typeof v === "object" && v.kind === "constant";
/** Empty or constant: a mapping that reads no column. */
export const isFixedMapping = (v) => isEmptyMapping(v) || isConstantMapping(v);

/** `"enterprise" · same for every row` / `Empty on every row` (pure). */
export function fixedMappingLabel(v) {
  if (isEmptyMapping(v)) return "Empty on every row";
  if (isConstantMapping(v)) return `"${v.value}" · same for every row`;
  return null;
}

const columnIdOf = (value) =>
  value && typeof value === "object"
    ? null
    : String(value ?? "").split(JSON_PATH_SEPARATOR)[0] || null;

/**
 * True when `value` points at a column that exists in `columns`, or is
 * `{kind: "empty"}`, or a `{kind: "constant"}` with text typed.
 */
export function isValidMapping(value, columns = []) {
  if (isEmptyMapping(value)) return true;
  if (isConstantMapping(value)) return String(value.value ?? "") !== "";
  const id = columnIdOf(value);
  return Boolean(id) && columns.some((c) => c.id === id);
}

/** What `{{var}}` becomes on one row for a fixed mapping, else undefined. */
export function fixedValueOf(v) {
  if (isEmptyMapping(v)) return "";
  if (isConstantMapping(v)) return String(v.value ?? "");
  return undefined;
}

/**
 * Mapping for `variables`: keeps valid existing choices, auto-maps the
 * rest, drops variables that are gone. Unmatched variables stay empty.
 */
export function autoMapVariables(variables = [], columns = [], current = {}) {
  const out = {};
  [...new Set(variables)].forEach((v) => {
    if (isValidMapping(current?.[v], columns)) {
      out[v] = current[v];
      return;
    }
    const hit = autoMapVariable(v, columns);
    out[v] = hit ? hit.value ?? hit.columnId : "";
  });
  return out;
}

/** Same keys and values (order-insensitive). */
export function sameMapping(a = {}, b = {}) {
  const ka = Object.keys(a ?? {});
  const kb = Object.keys(b ?? {});
  return ka.length === kb.length && ka.every((k) => a[k] === b[k]);
}

/** `{total, mapped, unmapped: [variable]}` for one run. */
export function mappingStatus(variables = [], mapping = {}, columns = []) {
  const vars = [...new Set(variables)];
  const unmapped = vars.filter((v) => !isValidMapping(mapping?.[v], columns));
  return {
    total: vars.length,
    mapped: vars.length - unmapped.length,
    unmapped,
  };
}

/** Chip text: `All mapped` · `2/3 mapped` · `Not mapped`. */
export function mappingChipLabel({ total, mapped }) {
  if (!total) return "No variables";
  if (mapped === total) return "All mapped";
  if (mapped === 0) return "Not mapped";
  return `${mapped}/${total} mapped`;
}

/**
 * IT-R4 G1 P1: the one unmapped sentence (Data step, stepper, Review):
 * `{{customer_tier}} has no column. Pick a column, or leave it empty.` ·
 * `2 variables have no column: customer_tier, customer_email.`
 */
export function unmappedMessage(unmapped = []) {
  if (!unmapped.length) return "";
  const n = unmapped.length;
  if (n === 1)
    return `{{${unmapped[0]}}} has no column. Pick a column, or leave it empty.`;
  return `${n} variables have no column: ${unmapped.join(", ")}.`;
}

/** `Leave empty` · `Leave both empty` · `Leave all 3 empty`. */
export function leaveEmptyLabel(n) {
  if (n <= 1) return "Leave empty";
  if (n === 2) return "Leave both empty";
  return `Leave all ${n} empty`;
}

/** `mapping` with every variable in `variables` set to `{kind: "empty"}`. */
export function leaveEmpty(mapping = {}, variables = []) {
  const out = { ...(mapping ?? {}) };
  variables.forEach((v) => {
    out[v] = { ...EMPTY_MAPPING };
  });
  return out;
}

const tokensOf = (name) =>
  normalizeName(String(name ?? "").replace(/[.[\]$]+/g, "_"))
    .split("_")
    .filter((t) => t && !/^\d+$/.test(t));

/**
 * IT-R4 G1 P1: the one `Suggested` option for a variable: the column or
 * nested path that shares its last word and the most name tokens (`customer_tier` ->
 * `metadata.customer.tier`), ties broken by fewer extra tokens and then
 * better coverage. Null when nothing shares a token (pure).
 * @param {string} variable
 * @param {{value: string, label: string, coverage?: {present, total}}[]} options
 */
export function suggestedOption(variable, options = []) {
  const list = tokensOf(variable);
  const want = new Set(list);
  if (!want.size) return null;
  // The variable's last word is what it holds (`customer_tier` -> `tier`):
  // an option must share it, so `customer_goal` is never suggested.
  const head = list[list.length - 1];
  let best = null;
  (options ?? []).forEach((o) => {
    const have = tokensOf(o.label);
    if (!have.includes(head)) return;
    const shared = new Set(have.filter((t) => want.has(t))).size;
    if (!shared) return;
    const extra = have.length - shared;
    const cov = o.coverage?.total ? o.coverage.present / o.coverage.total : 1;
    const score = [shared, -extra, cov];
    const better =
      !best ||
      score[0] > best.score[0] ||
      (score[0] === best.score[0] &&
        (score[1] > best.score[1] ||
          (score[1] === best.score[1] && score[2] > best.score[2])));
    if (better) best = { option: o, score };
  });
  return best?.option ?? null;
}

/**
 * M9 (W6 integration integration H-V): the text a mapping value shows in the column
 * picker. An option's own label when one lists the value; else, for a path
 * no option lists (a conversation turn path such as `[-1]`), the column's
 * name plus the path (`conversation[-1]`), never the raw column id. A value
 * whose column is not in the list keeps its own text (pure).
 * @param {*} value
 * @param {{value: string, label: string, path?: boolean}[]} options
 */
export function mappingValueLabel(value, options = []) {
  const list = options ?? [];
  const listed = list.find((o) => o.value === value);
  if (listed) return listed.label;
  if (typeof value !== "string") return "";
  const { colId, path } = fromLegacyMapping(value);
  const column = list.find((o) => !o.path && o.value === colId);
  if (!column) return value;
  if (!path) return column.label;
  return `${column.label}${path.startsWith("[") ? "" : "."}${path}`;
}

/**
 * Unique `{{name}}` variables used by a prompt's messages (content as a
 * string or `[{type: "text", text}]` parts), in order of appearance.
 */
export function variablesInMessages(messages = []) {
  const out = [];
  (Array.isArray(messages) ? messages : []).forEach((m) => {
    const content = m?.content;
    const parts = Array.isArray(content)
      ? content.map((c) => (typeof c === "string" ? c : c?.text ?? ""))
      : [typeof content === "string" ? content : ""];
    parts.forEach((t) =>
      variablesInTemplate(t).forEach((v) => {
        if (!out.includes(v)) out.push(v);
      }),
    );
  });
  return out;
}

/** Unique `{{name}}` variables in a template, in order of appearance. */
export function variablesInTemplate(text) {
  const out = [];
  String(text ?? "").replace(/{{\s*([^{}]+?)\s*}}/g, (_, name) => {
    if (!out.includes(name)) out.push(name);
    return _;
  });
  return out;
}

/**
 * Text of a mapped value on a table row (`row[colId].cell_value`), for the
 * `Value on Row 12` column. Objects are JSON-stringified.
 */
export function mappedValueOnRow(row, value) {
  if (isFixedMapping(value)) return fixedValueOf(value);
  if (!row || !value) return null;
  const { colId, path } = fromLegacyMapping(value);
  const cell = row?.[colId];
  const raw = cell && typeof cell === "object" ? cell.cell_value : cell;
  // W6 A4: a turn path on a conversation reads its turns (the run's reader).
  const conv = mappedConversationValue(value, raw);
  if (conv) return conv.found ? conv.text : null;
  let v = raw;
  if (path) {
    const r = resolvePath(raw, path.startsWith("[") ? path : `$.${path}`);
    v = !r.found ? undefined : r.multi ? r.values : r.value;
  }
  if (v == null) return null;
  return typeof v === "string" ? v : JSON.stringify(v);
}

/**
 * Coverage of a mapping across the sampled rows, from the json-schema's
 * typed `paths[]`: `{present, total, missingRowIds?}`, or null for a plain
 * column / an unknown path.
 * @param {string} value  mapping value (`<colId>` or `<colId>::$.<path>`)
 * @param {object} [schemas]  json-schema response (`{colId: {paths}}`)
 */
export function mappingCoverage(value, schemas) {
  if (isFixedMapping(value)) return null;
  const { colId, path } = fromLegacyMapping(value);
  if (!colId || !path) return null;
  const entry = schemas?.[colId];
  const parsed = parsePath(path.startsWith("[") ? path : `$.${path}`);
  if (!entry?.paths || !parsed.ok) return null;
  const rel = formatPath(null, parsed.steps);
  const hit = entry.paths.find((p) => p.rel === rel);
  if (!hit) return null;
  return {
    present: hit.coverage.present,
    total: hit.coverage.total,
    missingRowIds: hit.missing_row_ids ?? null,
    kinds: hit.kinds,
  };
}

// ------------------------------------------------- IT-R3 G5 P3: eval keys

const OUTPUT_KEYS = new Set([
  "output",
  "response",
  "actual",
  "actual_output",
  "answer",
  "completion",
  "generated",
]);
const EXPECTED_KEYS = new Set([
  "expected",
  "expected_output",
  "expected_response",
  "expected_reply",
  "reference",
  "ground_truth",
  "target",
]);

/**
 * `Add` on an existing eval maps its keys without a form (pure): `output`
 * and its synonyms → the run output, expected / reference → the dataset's
 * expected column (`referenceColumnId`, else the first `expected` role),
 * anything else → the same-name column, a synonym, or the first input
 * column. Values are column ids or `output`. Keys left unmatched are
 * omitted (the pencil maps them).
 * @param {string[]} keys  the eval's required keys (`[]` → `{output}`)
 * @param {{id: string, name: string, role?: string}[]} columns
 * @param {{referenceColumnId?: string}} [opts]
 */
export function autoMapEvalKeys(keys = [], columns = [], opts = {}) {
  const list = keys?.length ? [...new Set(keys)] : ["output"];
  const out = {};
  const input =
    columns.find((c) => c.role === "input") ??
    columns.find((c) =>
      INPUT_SYNONYMS.includes(String(c.name ?? "").toLowerCase()),
    ) ??
    null;
  const expected =
    columns.find((c) => c.id === opts.referenceColumnId) ??
    columns.find((c) => c.role === "expected") ??
    null;
  list.forEach((key) => {
    const k = normalizeName(key);
    if (OUTPUT_KEYS.has(k)) {
      out[key] = "output";
      return;
    }
    if (EXPECTED_KEYS.has(k) && expected) {
      out[key] = expected.id;
      return;
    }
    const hit = autoMapVariable(key, columns);
    if (hit) {
      out[key] = hit.value ?? hit.columnId;
      return;
    }
    if (["input", "query", "question", "prompt", "user_input"].includes(k))
      if (input) out[key] = input.id;
  });
  return out;
}

/** Wizard names for picker rows: `correctness_vs_reference` → `Correctness`. */
const EVAL_NAME_ALIASES = {
  correctness_vs_reference: "Correctness",
  groundedness_policy_kb: "Groundedness",
  tone_on_brand: "Tone",
};
export function evalDisplayName(name) {
  const raw = String(name ?? "");
  if (EVAL_NAME_ALIASES[raw]) return EVAL_NAME_ALIASES[raw];
  if (!/^[a-z0-9]+(_[a-z0-9]+)+$|^[a-z]+$/.test(raw)) return raw;
  const words = raw.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// ------------------------------------------------ modality (L3 G7 W6)
const MEDIA_OF_TYPE = Object.freeze({
  image: "image",
  images: "image",
  audio: "audio",
  video: "video",
  document: "document",
});
const READS_WORD = Object.freeze({
  text: "text",
  image: "images",
  audio: "audio",
  video: "video",
  document: "documents",
  json: "JSON",
});
const joinWords = (list) =>
  list.length > 1
    ? `${list.slice(0, -1).join(", ")} and ${list.at(-1)}`
    : list[0] ?? "";

/** The media a mapped value sends (`image` / `audio` / …), null for text. */
export function mappedModality(value, columns = []) {
  if (value == null || typeof value === "object") return null;
  const s = String(value);
  if (!s || s.includes(JSON_PATH_SEPARATOR)) return null;
  const col = (columns ?? []).find((c) => c.id === s);
  return MEDIA_OF_TYPE[col?.data_type] ?? null;
}

/**
 * Per run, the mapped columns whose modality the run's model cannot read
 * (pure). `runs`: `[{letter, model, inputs}]` (`inputs` from the model's
 * `capabilities.inputs`; a run without them is not checked). Returns
 * `[{letter, model, variable, column, modality, text}]`, e.g. "gpt-4.1-mini
 * reads text and images: call_audio is audio".
 */
export function modalityWarnings(runs = [], mapping = {}, columns = []) {
  const out = [];
  (runs ?? []).forEach((run) => {
    const inputs = run?.inputs ?? [];
    if (!inputs.length) return;
    Object.entries(mapping ?? {}).forEach(([variable, value]) => {
      const modality = mappedModality(value, columns);
      if (!modality || inputs.includes(modality)) return;
      const column = (columns ?? []).find((c) => c.id === String(value));
      const reads = joinWords(inputs.map((i) => READS_WORD[i] ?? i));
      out.push({
        letter: run.letter ?? null,
        model: run.model,
        variable,
        column: column?.name ?? variable,
        modality,
        text: `${run.model} reads ${reads}: ${column?.name ?? variable} is ${modality}`,
      });
    });
  });
  return out;
}
