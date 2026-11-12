// W6 B5 (W6-15): a variable retrieved at run time from a knowledge base.
// Pure; the wizard card, the create body and the mock run (sources/
// context.js) read the one shape:
//   context_source: {variable, kb_id, top_k, query_column}
// `query_column` is the column whose text is the query on each row. The
// chunks a try retrieved are stored on its record as
//   retrieved: [{kb, chunk_id, text, score}]
// and sent as the variable's value (retrievedText).

const s = (v) => String(v ?? "").trim();

/**
 * The normalized context source, or null when a part is missing. top_k is
 * a whole number of at least 1 (the user picks it; no default here).
 */
export function contextSourceOf(raw) {
  if (!raw || typeof raw !== "object") return null;
  const variable = s(raw.variable);
  const kbId = s(raw.kb_id ?? raw.kbId);
  const query = s(raw.query_column ?? raw.queryColumn);
  const topK = Math.floor(Number(raw.top_k ?? raw.topK));
  if (!variable || !kbId || !query || !(topK >= 1)) return null;
  return { variable, kb_id: kbId, top_k: topK, query_column: query };
}

/**
 * What blocks a context source, in the order the field shows it.
 * @param {object} raw  the card's contextSource
 * @param {{variables?: string[], columns?: {id, name}[], kbs?: {id, name}[]}} ctx
 * @returns {string[]}
 */
export function contextSourceIssues(raw, { variables, columns, kbs } = {}) {
  if (!raw) return [];
  const out = [];
  const variable = s(raw.variable);
  if (!variable) out.push("Pick the variable to retrieve.");
  else if (Array.isArray(variables) && !variables.includes(variable))
    out.push(`{{${variable}}} is not in the template.`);
  const kbId = s(raw.kb_id ?? raw.kbId);
  if (!kbId) out.push("Pick a knowledge base.");
  else if (Array.isArray(kbs) && !kbs.some((k) => k.id === kbId))
    out.push("That knowledge base no longer exists. Pick another one.");
  const topK = Math.floor(Number(raw.top_k ?? raw.topK));
  if (!(topK >= 1)) out.push("Top must be a whole number of 1 or more.");
  const query = s(raw.query_column ?? raw.queryColumn);
  if (!query) out.push("Pick the column the query comes from.");
  else if (Array.isArray(columns) && !columns.some((c) => c.id === query))
    out.push("The query column is not in this dataset.");
  return out;
}

/** The text a try sends as the variable: each chunk on its own line. */
export const retrievedText = (chunks) =>
  (chunks ?? [])
    .map((c) => s(c?.text))
    .filter(Boolean)
    .join("\n");

/**
 * The line the run's identity and Review show:
 * `{{context}} ← Refund policy · top 3 · query from customer_message`.
 */
export function contextSourceSummary(cs, { kbName, queryName } = {}) {
  const c = contextSourceOf(cs);
  if (!c) return null;
  return `{{${c.variable}}} ← ${kbName ?? "knowledge base"} · top ${c.top_k} · query from ${queryName ?? c.query_column}`;
}
