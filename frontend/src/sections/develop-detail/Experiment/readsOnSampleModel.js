// W1-5 (DS-04): ONE table of what each run and each eval reads, checked on
// the chosen sample row. Pure: the Data step's table and its test read it.
// A prompt card sends its messages with each {{variable}} filled from the
// row; what an eval reads comes from the scorer itself (eval-playground
// `reads_only` → readsOfInputsUsed), never from the card's mapping alone.
import { mappedValueOnRow } from "./variableMapping";

const OUTPUT = "output";
/** Eval keys that read the row's reference (L5 G1; one rule everywhere). */
export const REFERENCE_ROLE =
  /^(?:expected\w*|reference\w*|ground_?truth\w*|target)$/i;

const textOfContent = (content) =>
  Array.isArray(content)
    ? content.map((c) => (typeof c === "string" ? c : c?.text ?? "")).join("")
    : String(content ?? "");

/**
 * A prompt card's messages as sent on the row: each {{variable}} replaced
 * by its mapped value there (left as {{variable}} when unmapped or empty).
 * @returns {Array<{role: string, text: string, missing: string[]}>}
 */
export function renderPromptOnRow(messages, mapping, row) {
  return (Array.isArray(messages) ? messages : [])
    .map((m) => {
      const missing = [];
      const text = textOfContent(m?.content).replace(
        /{{\s*([^{}]+?)\s*}}/g,
        (all, name) => {
          const v = mappedValueOnRow(row, mapping?.[name]);
          if (v == null || !String(v).trim()) {
            missing.push(name);
            return all;
          }
          return v;
        },
      );
      return { role: m?.role ?? "user", text, missing };
    })
    .filter((m) => m.text.trim());
}

/**
 * What one eval card maps on the row, per input: `{input, column, value,
 * status: "ok"|"run_output"|"literal"|"missing", note}`. The card's mapping
 * only: the Data step's table shows the scorer's own reads
 * (readsOfInputsUsed), which also name what the rule adds (the policy KB,
 * the row's facts) and leave out what it never reads.
 * @param {object} evalItem  a userEvalMetrics entry (config.mapping or mapping)
 * @param {object} row       the sample row (`{<colId>: cell}`)
 * @param {Array<{id: string, name: string}>} columns
 */
export function evalReadsOnRow(evalItem, row, columns = []) {
  const mapping = evalItem?.config?.mapping ?? evalItem?.mapping ?? {};
  return Object.entries(mapping).map(([input, ref]) => {
    const isRef = REFERENCE_ROLE.test(input);
    if (ref === OUTPUT)
      return {
        input,
        column: "run output",
        value: null,
        status: "run_output",
        note: "each run's reply",
      };
    if (ref == null || ref === "")
      return {
        input,
        column: null,
        value: null,
        status: "missing",
        note: isRef
          ? "No reference column · skipped on every row"
          : "Not mapped",
      };
    const [id, ...rest] = String(ref).split("::");
    const col = columns.find((c) => c.id === id || c.name === id);
    // L7c g7 (wiz:card-mapping-preview): not a column: the one scorer reads
    // it as unmapped (a context input reads the policy knowledge base, an
    // input the row's input column); the eval card says the same.
    if (!col)
      return {
        input,
        column: String(ref),
        value: null,
        status: "literal",
        note: /^(?:context|knowledge|kb|policy|documents?)\w*$/i.test(input)
          ? "Not a dataset column · reads the policy knowledge base (and the row's expected reply)"
          : /^(?:input|question|message|query|request|prompt|instruction)\w*$/i.test(
                input,
              )
            ? "Not a dataset column · reads the row's input column"
            : "Not a dataset column",
      };
    const path = rest.length ? `::${rest.join("::")}` : "";
    const value = mappedValueOnRow(row, `${col.id}${path}`);
    const name = path ? `${col.name}${path.replace(/^::\$?/, "")}` : col.name;
    if (value == null || !String(value).trim())
      return {
        input,
        column: name,
        value: null,
        status: "missing",
        note: isRef
          ? "Empty on this row · skipped here (no expected value)"
          : "Empty on this row",
      };
    return { input, column: name, value, status: "ok", note: null };
  });
}

/**
 * W1-5: one eval's reads on the sample row, as the scorer resolved them
 * (`inputs_used` and `skipped` from eval-playground `reads_only`, the same
 * resolution each result cell records), as the table's rows: `{input,
 * column, value, status: "ok"|"run_output"|"missing", note}`, the run's
 * output first. A missing reference says the eval is skipped there, as the
 * scorer does.
 * @param {Object<string, {column: string|null, value: string|null}>} inputsUsed
 * @param {string|null} skipped  the scorer's skip on this row
 */
export function readsOfInputsUsed(inputsUsed = {}, skipped = null) {
  return Object.entries(inputsUsed ?? {})
    .sort(([a], [b]) => (a === OUTPUT ? -1 : b === OUTPUT ? 1 : 0))
    .map(([input, used]) => {
      if (input === OUTPUT && used?.column === "run output")
        return {
          input,
          column: "run output",
          value: null,
          status: "run_output",
          note: "each run's reply",
        };
      const isRef = REFERENCE_ROLE.test(input);
      if (used?.column == null)
        return {
          input,
          column: null,
          value: null,
          status: "missing",
          note:
            isRef && skipped === "no_reference_column"
              ? "No reference column · skipped on every row"
              : "Not mapped",
        };
      const value = used.value ?? null;
      if (value == null || !String(value).trim())
        return {
          input,
          column: used.column,
          value: null,
          status: "missing",
          note:
            isRef && skipped === "no_expected_value"
              ? "Empty on this row · skipped here (no expected value)"
              : "Empty on this row",
        };
      return { input, column: used.column, value, status: "ok", note: null };
    });
}

/**
 * W1-5: a saved mapping as the eval editor shows it: each column id (or
 * `id::path`) by the column's name, so the editor never shows a raw id.
 * Values that are not a column (the run's output, a name) stay as they are.
 * @param {object} mapping  `{<input>: <colId>|<name>|null}`
 * @param {Array<{field?: string, id?: string, headerName?: string,
 *   name?: string}>} columns
 */
export function mappingWithNames(mapping, columns = []) {
  if (!mapping || typeof mapping !== "object") return mapping;
  return Object.fromEntries(
    Object.entries(mapping).map(([input, ref]) => {
      if (typeof ref !== "string") return [input, ref];
      const [id, ...rest] = ref.split("::");
      const col = columns.find((c) => (c.field ?? c.id) === id);
      const name = col?.headerName ?? col?.name;
      if (!name || name === id) return [input, ref];
      const path = rest.join("::").replace(/^\$\.?/, "");
      return [input, path ? `${name}.${path}` : name];
    }),
  );
}

/**
 * W1-5: Expected answers drives every eval's reference-role inputs: when it
 * changes from `prev` to `next`, each reference key that read `prev` (or
 * read no column) reads `next` (null: None, the eval is skipped on every
 * row). Returns the evals, the same array when nothing changed. One rule
 * for the Data step (where the column is picked) and the Evaluations step.
 */
export function remapReference(evals, prev, next) {
  let any = false;
  const out = (evals ?? []).map((f) => {
    const mapping = f?.config?.mapping ?? f?.mapping ?? {};
    let changed = false;
    const nextMap = { ...mapping };
    Object.entries(mapping).forEach(([k, v]) => {
      if (!REFERENCE_ROLE.test(k)) return;
      const col = v == null ? null : String(v).split("::")[0];
      if (col !== (prev ?? null) && v != null) return;
      nextMap[k] = next || null;
      changed = true;
    });
    if (!changed) return f;
    any = true;
    return {
      ...f,
      mapping: nextMap,
      config: { ...(f?.config ?? {}), mapping: nextMap },
    };
  });
  return any ? out : evals;
}

/**
 * v2.1 (wiz:expected-answers): who reads the Expected answers column, in
 * the scorer's own order (judgeScript.referenceOf): an eval whose card maps
 * an expected input reads its mapped column (this pick remaps it); an eval
 * that maps none reads the column marked Expected. Pure.
 * @param {object[]} evals  the form's eval cards
 * @param {{name: string, role?: string}[]} columns  user columns
 * @returns {string}
 */
export function referenceHelpOf(evals = [], columns = []) {
  const names = (evals ?? [])
    .filter((f) =>
      Object.keys(f?.config?.mapping ?? f?.mapping ?? {}).some((k) =>
        REFERENCE_ROLE.test(k),
      ),
    )
    .map((f) => f?.name ?? f?.evalTemplateName)
    .filter(Boolean);
  const marked = (columns ?? []).find((c) => c?.role === "expected");
  const others = (evals ?? []).length - names.length;
  return [
    names.length
      ? `Read by the evaluations that map an expected input (${names.join(", ")}).`
      : "Read by the evaluations that map an expected input.",
    others > 0
      ? `An evaluation that maps none reads ${marked ? `the column marked Expected (${marked.name})` : "no expected value"}.`
      : null,
  ]
    .filter(Boolean)
    .join(" ");
}
