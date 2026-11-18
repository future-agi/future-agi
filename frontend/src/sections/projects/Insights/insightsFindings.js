// The executive summary as a list of findings. A run written by the current
// engine carries `summary.findings` (headline, detail, action, severity, refs,
// evidence); older runs only have `summary.key_points[].text`, which is turned
// into the same shape: the first sentence is the headline, the rest the detail.

export const SEVERITIES = ["high", "medium", "low"];

const REF_IN_TEXT = /#(\d+)/g;

// A sentence ends at . ! or ? (optionally closed by a quote or bracket) that is
// followed by whitespace and a capital, a digit or an opening quote. Decimals
// ("11.7s") and "#3." inside a citation group never qualify.
const SENTENCE_END = /[.!?]["”’)]*(?=\s+["“(]?[A-Z0-9])/g;

/** "First sentence. The rest." -> {headline: "First sentence.", rest: "The rest."} */
export function splitFirstSentence(text) {
  const source = String(text || "").trim();
  if (!source) return { headline: "", rest: "" };
  for (const match of source.matchAll(SENTENCE_END)) {
    const end = match.index + match[0].length;
    // An abbreviation ("e.g.", "vs.") is too short to end a headline.
    const before = source.slice(0, match.index);
    if (/\b(?:e\.g|i\.e|vs|etc|approx|incl)$/i.test(before)) continue;
    return {
      headline: source.slice(0, end).trim(),
      rest: source.slice(end).trim(),
    };
  }
  return { headline: source, rest: "" };
}

const toRefs = (refs) =>
  (Array.isArray(refs) ? refs : [])
    .map((r) => ({ ref: Number(r?.ref), unitId: r?.unit_id || "" }))
    .filter((r) => Number.isInteger(r.ref));

const toEvidence = (evidence) =>
  (Array.isArray(evidence) ? evidence : [])
    .filter((e) => e && typeof e.quote === "string" && e.quote.trim())
    .map((e) => ({ unitId: e.unit_id || "", quote: e.quote.trim() }));

/** Numbers cited as "#n" anywhere in the given strings. */
export function refsInText(...texts) {
  const found = new Set();
  texts.forEach((text) => {
    for (const match of String(text || "").matchAll(REF_IN_TEXT))
      found.add(Number(match[1]));
  });
  return found;
}

/**
 * Findings of a summary, in order:
 *   {headline, detail, action, severity, categoryIds, refs: [{ref, unitId}],
 *    trailingRefs: [n] (cited but not mentioned in the text), evidence, legacy}
 */
export function normalizeFindings(summary) {
  if (!summary || summary.error) return [];
  if (Array.isArray(summary.findings) && summary.findings.length) {
    return summary.findings
      .filter((f) => f && (f.headline || f.detail))
      .map((f) => {
        const refs = toRefs(f.refs);
        const mentioned = refsInText(f.headline, f.detail, f.action);
        return {
          headline: String(f.headline || "").trim(),
          detail: String(f.detail || "").trim(),
          action: String(f.action || "").trim(),
          severity: SEVERITIES.includes(f.severity) ? f.severity : null,
          categoryIds: Array.isArray(f.category_ids) ? f.category_ids : [],
          refs,
          trailingRefs: [
            ...new Set(refs.map((r) => r.ref).filter((n) => !mentioned.has(n))),
          ],
          evidence: toEvidence(f.evidence),
          legacy: false,
        };
      });
  }
  return (summary.key_points || [])
    .filter((point) => point && String(point.text || "").trim())
    .map((point) => {
      const { headline, rest } = splitFirstSentence(point.text);
      const refs = toRefs(point.refs);
      const mentioned = refsInText(point.text);
      return {
        headline,
        detail: rest,
        action: "",
        severity: null,
        categoryIds: Array.isArray(point.category_ids)
          ? point.category_ids
          : [],
        refs,
        trailingRefs: [
          ...new Set(refs.map((r) => r.ref).filter((n) => !mentioned.has(n))),
        ],
        evidence: [],
        legacy: true,
      };
    });
}

// Whether quotes on the findings were checked against the examples they
// cite: `summary.verification.quotes`, or for a run written before the
// summary said so, whether any finding carries a quote (only the Insights
// agent's findings do, and their quotes are checked).
const quotesChecked = (summary) =>
  typeof summary?.verification?.quotes === "boolean"
    ? summary.verification.quotes
    : (Array.isArray(summary?.findings) ? summary.findings : []).some(
        (f) => toEvidence(f?.evidence).length > 0,
      );

/**
 * The line under the findings: what they were checked against and what was
 * left out. {text, checked, verifier} where checked is false when the
 * summary or the independent verifier says the findings did not hold up, and
 * verifier is true when an independent model re-checked them. It names only
 * the checks that ran (`summary.verification`): the built-in engine checks a
 * finding's numbers against the statistics and nothing else.
 */
export function verificationNote(summary, count) {
  const verification = summary?.verification || {};
  const dropped =
    (Number(summary?.dropped_points) || 0) +
    (Number(verification.request_evidence_discarded) || 0);
  const unchecked = Number(verification.investigations_failed) || 0;
  const tail = [
    dropped > 0 && `${dropped} dropped`,
    unchecked > 0 && `${unchecked} could not be investigated`,
  ]
    .filter(Boolean)
    .map((part) => ` · ${part}`)
    .join("");
  const verdict = verification.model_verifier;
  if (summary?.verified === false || (verdict && verdict !== "passed"))
    return {
      checked: false,
      verifier: Boolean(verdict),
      text: `These findings could not be confirmed against the report's statistics${tail}`,
    };
  const noun = count === 1 ? "finding" : "findings";
  const against = quotesChecked(summary)
    ? "the report's statistics and the cited examples"
    : "the report's statistics";
  if (verdict === "passed")
    return {
      checked: true,
      verifier: true,
      text: `${count} ${noun} checked by an independent verifier against the statistics and cited examples${tail}`,
    };
  return {
    checked: true,
    verifier: false,
    text: `${count} ${noun} checked against ${against}${tail}`,
  };
}

/** A highlight's card title: its own title, else its summary cut to 90 characters. */
export function highlightTitle(highlight, max = 90) {
  const own = String(highlight?.title || "").trim();
  const base = own || splitFirstSentence(highlight?.summary).headline;
  if (base.length <= max) return base;
  const cut = base.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).replace(/[\s,;:.]+$/, "")}…`;
}

// More citations than this on one finding fold into a "+N".
export const MAX_FINDING_REFS = 4;

/**
 * Which citations of a finding stay visible. Refs count in reading order
 * (headline, detail, the refs that trail the detail, action), each number
 * once; past `max` the rest fold into one "+N" placed where the first
 * hidden ref would have been.
 *   -> {hidden: Set<n>, more: [n, ...], at: "headline" | "detail" |
 *       "trailing" | "action" | null}
 */
export function citationBudget(
  finding,
  isKnown = () => true,
  max = MAX_FINDING_REFS,
) {
  const shown = new Set();
  const hidden = new Set();
  let at = null;
  const visit = (place, refs) =>
    refs.forEach((n) => {
      if (shown.has(n) || hidden.has(n) || !isKnown(n)) return;
      if (shown.size < max) shown.add(n);
      else {
        hidden.add(n);
        at = at || place;
      }
    });
  const inText = (text) =>
    [...String(text || "").matchAll(/#(\d+)/g)].map((m) => Number(m[1]));
  visit("headline", inText(finding?.headline));
  visit("detail", inText(finding?.detail));
  visit("trailing", finding?.trailingRefs || []);
  visit("action", inText(finding?.action));
  return { hidden, more: [...hidden], at };
}
