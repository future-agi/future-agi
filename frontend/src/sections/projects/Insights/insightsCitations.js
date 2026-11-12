// The executive summary cites examples as "#n" ("... (#3, #11)."). The report
// carries the map from n to the unit it means; these helpers turn the text
// into segments so each citation renders as a clickable chip.

// A parenthesised run of citations "(#3, #11)" or "(#1 and #2)", or a lone "#7".
const CITATION = /\(\s*#\d+(?:\s*(?:,|;|and|&)\s*#\d+)*\s*\)|#\d+/g;

/**
 * Map every citable number to {unitId, categoryId} from summary.examples,
 * summary.findings[].refs, summary.key_points[].refs and summary.highlights.
 */
export function buildRefIndex(summary) {
  const index = new Map();
  const add = (ref, unitId, categoryId) => {
    const n = Number(ref);
    if (!Number.isInteger(n) || !unitId) return;
    const known = index.get(n) || {};
    index.set(n, {
      unitId: known.unitId || unitId,
      categoryId: known.categoryId || categoryId || "",
    });
  };
  (summary?.examples || []).forEach((e) =>
    add(e.ref, e.unit_id, e.category_id),
  );
  (summary?.findings || []).forEach((finding) =>
    (finding?.refs || []).forEach((r) => add(r.ref, r.unit_id)),
  );
  (summary?.key_points || []).forEach((point) =>
    (point.refs || []).forEach((r) => add(r.ref, r.unit_id)),
  );
  (summary?.highlights || []).forEach((h) =>
    add(h.ref, h.unit_id, h.category_id),
  );
  return index;
}

/**
 * Split text into [{type: "text", text}] and [{type: "refs", refs: [n, ...]}]
 * segments. A citation whose numbers are not all known stays plain text, so a
 * stray "#2" in prose never becomes a dead chip.
 */
export function parseCitations(text, refIndex) {
  const source = String(text || "");
  const segments = [];
  let last = 0;
  const pushText = (value) => {
    if (!value) return;
    const previous = segments[segments.length - 1];
    if (previous?.type === "text") previous.text += value;
    else segments.push({ type: "text", text: value });
  };
  for (const match of source.matchAll(CITATION)) {
    const refs = [...match[0].matchAll(/#(\d+)/g)].map((m) => Number(m[1]));
    const known = refs.every((n) => !refIndex || refIndex.has(n));
    pushText(source.slice(last, match.index));
    if (known) segments.push({ type: "refs", refs });
    else pushText(match[0]);
    last = match.index + match[0].length;
  }
  pushText(source.slice(last));
  return segments;
}

/**
 * What a citation's hover card shows, by unit id: {title, summary, reason,
 * quote, categoryId}. Highlights carry a title, summary and quote; findings
 * carry the quotes that support them. A unit with neither is fetched on hover.
 */
export function buildPreviewIndex(summary) {
  const byUnit = new Map();
  const merge = (unitId, patch) => {
    if (!unitId) return;
    const known = byUnit.get(unitId) || {};
    const next = { ...known };
    Object.entries(patch).forEach(([key, value]) => {
      if (value && !next[key]) next[key] = value;
    });
    byUnit.set(unitId, next);
  };
  (summary?.highlights || []).forEach((h) =>
    merge(h.unit_id, {
      title: h.title,
      summary: h.summary,
      reason: h.reason,
      quote: h.quote,
      categoryId: h.category_id,
    }),
  );
  (summary?.findings || []).forEach((finding) =>
    (finding?.evidence || []).forEach((e) =>
      merge(e?.unit_id, { quote: e?.quote }),
    ),
  );
  return byUnit;
}

/**
 * Citation numbers for the units an Ask answer cites. A unit the report
 * already cites keeps its number; any other gets the next free one, and the
 * same unit keeps its number across the whole thread.
 *   assignAskRefs(answers, refIndex, known) -> Map unitId -> n
 */
export function assignAskRefs(answers, refIndex, known = new Map()) {
  const numbers = new Map(known);
  const byUnit = new Map();
  (refIndex ? [...refIndex.entries()] : []).forEach(([n, entry]) => {
    if (entry?.unitId && !byUnit.has(entry.unitId)) byUnit.set(entry.unitId, n);
  });
  let next =
    Math.max(
      0,
      ...(refIndex ? [...refIndex.keys()] : []),
      ...numbers.values(),
    ) + 1;
  (answers || []).forEach((paragraph) =>
    (paragraph?.unit_ids || []).forEach((unitId) => {
      if (!unitId || numbers.has(unitId)) return;
      if (byUnit.has(unitId)) numbers.set(unitId, byUnit.get(unitId));
      else numbers.set(unitId, next++);
    }),
  );
  return numbers;
}
