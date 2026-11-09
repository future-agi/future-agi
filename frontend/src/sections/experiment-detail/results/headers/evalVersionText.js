// W1-3 (EV-03): the words of an eval's version on the results page: the
// "v3 · v4 available" notice, the flip preview's line, and the refusal to
// compare two runs scored with different versions. Pure.

const numberOf = (v) => {
  const n = Number(String(v ?? "").replace(/^v/i, ""));
  return Number.isFinite(n) && n >= 1 ? n : null;
};

/**
 * The runs in view on an older version whose definition the template's
 * newest changes: the server names them (`outdated_runs`; a newer version
 * that changes nothing is never announced).
 */
export function olderRunsOf(def, runLabels = {}) {
  const latest = numberOf(def?.latest_version);
  if (!latest) return [];
  const outdated = Array.isArray(def?.outdated_runs)
    ? new Set(def.outdated_runs)
    : null;
  return Object.entries(def?.versions ?? {})
    .filter(([runId, v]) =>
      outdated
        ? outdated.has(runId)
        : numberOf(v) != null && numberOf(v) < latest,
    )
    .map(([runId, version]) => ({
      runId,
      version,
      label: runLabels[runId] ?? null,
    }));
}

/** "v3 · v4 available". */
export const driftLabel = (version, latest) =>
  `${version} · ${latest} available`;

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * The preview's verdict line: "5 rows would flip: 3 Fail → Pass, 2 Pass →
 * Fail"; when a row flips on several tries the counts say "scores".
 */
export function flipLine(p) {
  if (!p) return "";
  const rows = p.would_flip?.length ?? 0;
  if (!rows) return "No row would flip";
  const cells = (p.fail_to_pass ?? 0) + (p.pass_to_fail ?? 0);
  const unit = cells === rows ? "" : " scores";
  const parts = [
    p.fail_to_pass ? `${p.fail_to_pass}${unit} Fail → Pass` : null,
    p.pass_to_fail ? `${p.pass_to_fail}${unit} Pass → Fail` : null,
  ].filter(Boolean);
  return `${plural(rows, "row")} would flip: ${parts.join(", ")}`;
}

/** One line per changed field: "Pass bar: 0.7 → 0.9". */
export const changeLines = (changes = []) =>
  changes
    .filter((c) => c.field !== "instructions")
    .map((c) => `${c.label}: ${c.from ?? "none"} → ${c.to ?? "none"}`);

/**
 * The header's note when a run's delta is refused: two versions.
 * @returns {{label: string, tooltip: string}|null}
 */
export function notComparedNote(entry, { runLabel, baseLabel } = {}) {
  if (entry?.not_comparable_reason !== "eval_versions_differ") return null;
  const { run, baseline } = entry.versions_compared ?? {};
  return {
    label: "Not compared",
    tooltip: `Not compared: ${runLabel ?? "this run"} scored with ${run}, ${baseLabel ?? "the baseline"} with ${baseline}. Re-score one on the other's version, or compare anyway.`,
  };
}

/**
 * W1-3: the run a refused delta can re-score so the two compare: the one on
 * the older version, re-scored on the newer, when the newer is one of the
 * template's versions (not past its newest). null otherwise.
 * @returns {{runId: string, version: string}|null}
 */
export function rescoreTargetOf(entry, { runId, baseId, latest } = {}) {
  const { run, baseline } = entry?.versions_compared ?? {};
  const a = numberOf(run);
  const b = numberOf(baseline);
  const top = numberOf(latest);
  if (!a || !b || a === b || !top || Math.max(a, b) > top) return null;
  // L7c g7: an edit made in an experiment ("v2.1") is not a template
  // version another run can be re-scored on.
  if (!Number.isInteger(a) || !Number.isInteger(b)) return null;
  if (a < b) return runId ? { runId, version: baseline } : null;
  return baseId ? { runId: baseId, version: run } : null;
}

/** The header's note when the user chose to compare two versions anyway. */
export function mixedNote(entry) {
  if (!entry?.mixed_versions) return null;
  const { run, baseline } = entry.versions_compared ?? {};
  return {
    label: "mixed versions",
    tooltip: `Compared anyway: ${baseline} (baseline) vs ${run}. The two runs were scored by different eval versions.`,
  };
}
