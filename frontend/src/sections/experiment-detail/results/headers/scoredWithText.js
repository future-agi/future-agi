// W1-2: the results eval header's hover text: what each run's cells were
// scored with (version, pass bar, judge). Pure.

const partOf = (name, version, s) =>
  [
    version ? `${name} ${version}` : name,
    s?.threshold != null ? `Pass ≥ ${s.threshold}` : null,
    s?.judge_model ? `judge ${s.judge_model}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

/**
 * W1-2: what an eval's cells were scored with, per run when they differ:
 * `Correctness v2 · Pass ≥ 0.95 · judge gpt-4o`; `#2A: … · #2A·2: …`. A run
 * Score with… re-scored on some rows only (`parts`) names each definition
 * with its rows: `#2B·2: Correctness v2 · judge turing_large (235 rows) +
 * Correctness v3 · judge gpt-4o (3 rows)`.
 */
export function scoredWithText(name, versions, scoredWith, runLabels = {}) {
  const lineOf = (runId) => {
    const s = scoredWith?.[runId] ?? {};
    if (Array.isArray(s.parts) && s.parts.length > 1)
      return s.parts
        .map(
          (p) =>
            `${partOf(name, p.version, p)} (${p.rows} ${p.rows === 1 ? "row" : "rows"})`,
        )
        .join(" + ");
    return partOf(name, versions?.[runId], s);
  };
  const ids = Object.keys(scoredWith ?? {});
  if (!ids.length) return name;
  const lines = [...new Set(ids.map(lineOf))];
  if (lines.length === 1 && ids.length === 1) return lines[0];
  if (lines.length === 1 && !ids.some((id) => scoredWith[id]?.parts))
    return lines[0];
  return ids.map((id) => `${runLabels[id] ?? id}: ${lineOf(id)}`).join(" · ");
}
