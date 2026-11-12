// W1-2 (LS-01): a rule-based draft judge from the expected column. It reads
// the column's values on the dataset, lists the kinds of facts they state
// with how many rows state each ("reference ids in 231 of 248 rows"), and
// turns the facts the author ticks into an LLM judge's instructions. It is
// a draft (labelled so): the author edits it before relying on it, and it
// is scored like any custom eval by the one scorer. Pure: values in, words
// out; the facts are text patterns, never a fixture's ids.

const FACTS = [
  {
    key: "ids",
    label: "reference ids",
    re: /\b[A-Z]{1,4}-\d{3,}\b/,
  },
  {
    key: "amounts",
    label: "amounts",
    re: /[$€£]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|dollars|euros|pounds)\b/i,
  },
  {
    key: "time_frames",
    label: "time frames",
    re: /\b\d+(?:\s?[-–]\s?\d+)?\s?(?:business\s|working\s)?(?:minutes?|hours?|days?|weeks?|months?)\b/i,
  },
  {
    key: "dates",
    label: "dates",
    re: /\b\d{4}-\d{2}-\d{2}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}\b|\b(?:Mon|Tues?|Wed(?:nes)?|Thu(?:rs)?|Fri|Sat(?:ur)?|Sun)(?:day)?\b/,
  },
  {
    key: "emails",
    label: "email addresses",
    re: /\b[\w.+-]+@[\w-]+\.[\w.]+\b/,
  },
  {
    key: "links",
    label: "links",
    re: /\bhttps?:\/\/\S+/i,
  },
];

const textOf = (v) =>
  v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);

/**
 * The kinds of facts the column's values state, most rows first.
 * @param {unknown[]} values  the column's value on each row
 * @returns {{key: string, label: string, rows: number, total: number}[]}
 */
export function draftFactsOf(values = []) {
  const texts = values.map(textOf).filter((t) => t.trim());
  return FACTS.map((f) => ({
    key: f.key,
    label: f.label,
    rows: texts.filter((t) => f.re.test(t)).length,
    total: texts.length,
  }))
    .filter((f) => f.rows > 0)
    .sort((a, b) => b.rows - a.rows || a.label.localeCompare(b.label));
}

/** `reference ids in 231 of 248 rows · amounts in 198 · dates in 77`. */
export function draftFactsLine(facts = []) {
  return facts
    .map((f, i) =>
      i === 0
        ? `${f.label} in ${f.rows} of ${f.total} rows`
        : `${f.label} in ${f.rows}`,
    )
    .join(" · ");
}

const joinAnd = (xs) =>
  xs.length <= 1
    ? xs[0] ?? ""
    : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/**
 * A template name the eval library accepts (lowercase, `_` / `-`):
 * `matches_<column>`; `prefix` "" names it from the text alone (an edited
 * draft name).
 */
export const draftNameOf = (column, prefix = "matches_") =>
  `${prefix}${
    String(column ?? "expected")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "") || "expected"
  }`;

/**
 * The draft judge for `column` from the ticked facts: its template name,
 * the name people see, its instructions and its draft label.
 */
export function draftJudgeOf({ column, picked = [] }) {
  const labels = picked.map((f) => f.label);
  const what = labels.length
    ? `states the same ${joinAnd(labels)} as {{expected}}`
    : "gives the same answer as {{expected}}";
  return {
    name: draftNameOf(column),
    displayName: `Matches ${column}`,
    instructions: `Pass if {{output}} ${what}. Fail if it leaves one out or states a different one.`,
    description: `Matches ${column}: the reply ${labels.length ? `states the same ${joinAnd(labels)}` : "gives the same answer"} as the expected answer.`,
    label: `Rule-based draft from ${column} · edit before saving`,
  };
}
