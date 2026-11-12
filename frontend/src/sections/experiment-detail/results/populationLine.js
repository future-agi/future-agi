import { fmtCount } from "src/sections/common/format/fmt";

/**
 * IT-R4 G3 (P1, rule 28): the results footer's one population line, when
 * the pair in view is not on the same rows — `9 rows both ran · candidate
 * on dataset v6, baseline on v5 · 247 of 286 tries scored`. Numbers are
 * the verdict's lead line (ResultsVerdict `onLead` meta: `bothRan`,
 * `notRun`, `partial`), so the footer and the verdict never disagree.
 * Null when both runs ran the same rows of the same version, all scored.
 * @param {{lead?: object, cand?: object, base?: object, noun?: string}} a
 * @returns {string|null}
 */
export function populationLine({ lead, cand, base, noun = "row" } = {}) {
  if (!lead || !Number.isFinite(lead.bothRan)) return null;
  const cv = cand?.dataset_version?.number ?? null;
  const bv = base?.dataset_version?.number ?? null;
  const versionsDiffer = cv != null && bv != null && cv !== bv;
  const partial = lead.partial?.total ? lead.partial : null;
  if (!versionsDiffer && !partial && !lead.notRun) return null;
  // IT-R5 G3 (S5): `19 rows both ran (v5: 238 rows, v6: 20 rows)` — each
  // version's own row count, so `Rows 239` and `19 both ran` add up.
  const rowsOf = (run) => {
    const n = run?.progress?.total ?? run?.row_count ?? null;
    return Number.isFinite(n) ? n : null;
  };
  const perVersion = versionsDiffer
    ? [
        { v: bv, n: rowsOf(base) },
        { v: cv, n: rowsOf(cand) },
      ].sort((a, b) => a.v - b.v)
    : null;
  const parts = [
    versionsDiffer && perVersion.every((x) => x.n != null)
      ? `${fmtCount(lead.bothRan, noun)} both ran (${perVersion.map((x) => `v${x.v}: ${fmtCount(x.n, noun)}`).join(", ")})`
      : `${fmtCount(lead.bothRan, noun)} both ran`,
  ];
  if (versionsDiffer && !perVersion.every((x) => x.n != null))
    parts.push(`candidate on dataset v${cv}, baseline on v${bv}`);
  else if (!versionsDiffer && (cv != null || bv != null))
    parts.push(`dataset v${cv ?? bv}`);
  if (partial)
    parts.push(
      `${fmtCount(partial.scored)} of ${fmtCount(partial.total, "try")} scored`,
    );
  return parts.join(" · ");
}
