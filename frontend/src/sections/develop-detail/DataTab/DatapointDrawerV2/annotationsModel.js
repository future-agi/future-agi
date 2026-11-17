// RV#18: a row's human labels grouped by label, with agreement. Pure.

/** One score's value as short text: `4/5`, `Up`, `Down`, `0.8`, `refund`. */
export function scoreText(type, value) {
  if (value == null) return "—";
  if (type === "star" && value.rating != null)
    return `${value.rating}/${value.max ?? 5}`;
  if (type === "thumbs_up_down" && value.value)
    return value.value === "up" ? "Up" : "Down";
  if (type === "numeric" && value.value != null) return String(value.value);
  if (type === "categorical" && value.selected != null)
    return Array.isArray(value.selected)
      ? value.selected.join(", ")
      : String(value.selected);
  if (type === "text" && value.text != null) return String(value.text);
  if (typeof value !== "object") return String(value);
  return "—";
}

/** Comparable key of a value (text labels never "agree"). */
const agreeKey = (type, value) =>
  type === "text" ? null : scoreText(type, value);

/**
 * `[{label, type, entries: [{id, annotator, text, at, notes}], agreement}]`
 * in first-seen order. `agreement`: `{state: "agree"|"split"|"single"|null,
 * text}` — `2 of 2 agree · Down`, `Split · 2/5 vs 4/5`, `Split · 2 × Up · 1 × Down`, `1 annotator`.
 */
export function groupScores(scores) {
  const byLabel = new Map();
  (scores ?? []).forEach((s) => {
    const label = s.label_name || "Label";
    if (!byLabel.has(label))
      byLabel.set(label, { label, type: s.label_type, entries: [] });
    byLabel.get(label).entries.push({
      id: s.id,
      annotator: s.annotator_name || s.annotator_email || "System",
      text: scoreText(s.label_type, s.value),
      key: agreeKey(s.label_type, s.value),
      at: s.updated_at ?? s.created_at ?? null,
      notes: s.notes || null,
    });
  });
  return [...byLabel.values()].map((g) => {
    const n = g.entries.length;
    let agreement = { state: null, text: "" };
    if (n === 1) agreement = { state: "single", text: "1 annotator" };
    else if (g.type !== "text") {
      const tally = new Map();
      g.entries.forEach((e) => tally.set(e.text, (tally.get(e.text) ?? 0) + 1));
      if (tally.size === 1)
        agreement = {
          state: "agree",
          text: `${n} of ${n} agree · ${g.entries[0].text}`,
        };
      else {
        const parts = [...tally];
        agreement = {
          state: "split",
          text: parts.every(([, c]) => c === 1)
            ? `Split · ${parts.map(([t]) => t).join(" vs ")}`
            : `Split · ${parts.map(([t, c]) => `${c} × ${t}`).join(" · ")}`,
        };
      }
    }
    return { ...g, agreement };
  });
}

/** Scores Section opens by itself up to this many (A1-31). */
export const SCORES_OPEN_MAX = 5;

/**
 * The collapsed scores Section still says its answer (A1-31):
 * `12 scores · 3 labels · last by Ravi 2h ago`.
 */
export function scoresSummary(scores, { now, relative } = {}) {
  const list = scores ?? [];
  if (!list.length) return "";
  const labels = new Set(list.map((s) => s.label_name || "Label")).size;
  const latest = list.reduce((a, b) => {
    const ta = Date.parse(a.updated_at ?? a.created_at ?? "") || 0;
    const tb = Date.parse(b.updated_at ?? b.created_at ?? "") || 0;
    return tb > ta ? b : a;
  });
  const who = latest.annotator_name || latest.annotator_email || "System";
  const at = latest.updated_at ?? latest.created_at;
  const when = at && relative ? ` ${relative(at, { now })}` : "";
  return `${list.length} score${list.length === 1 ? "" : "s"} · ${labels} label${labels === 1 ? "" : "s"} · last by ${who}${when}`;
}
