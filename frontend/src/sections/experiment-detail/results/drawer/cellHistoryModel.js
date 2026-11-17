// W1-1 (kept runs, notify DESIGN §1.2): a single cell re-run amends its run
// and keeps the cell's earlier results. The row view lists them as
// `Result 1 · Result 2 · Result 3 (now)`, each with who made it and when,
// and the run's details carry one activity line per amendment
// (`3 cells re-run by Asha · Oct 23, 10:42`). Pure.
import { fDateTime } from "src/utils/format-time";

const nameOf = (by) =>
  typeof by === "string" ? by : by?.name ?? by?.display_name ?? null;
const when = (at) => (at ? fDateTime(at) : null);

/**
 * The results of one try, oldest first, the current one last.
 * @param {object} t  an R3 `trials_detail` entry (with `history`)
 * @param {string[]} [keys]  eval keys to name (in order)
 * @returns {Array<{n: number, label: string, now: boolean, output: any,
 *   status: string|null, error: string|null, scores: string, meta: string}>}
 */
export function historyEntries(t, keys = null) {
  const list = Array.isArray(t?.history) ? t.history : [];
  if (!list.length) return [];
  const scoresText = (scores) =>
    Object.entries(scores ?? {})
      .filter(([k]) => !keys || keys.includes(k))
      .map(([, x]) => x?.display)
      .filter(Boolean)
      .join(" · ");
  const all = [
    ...list,
    {
      output: t.output,
      status: t.status,
      error: t.error,
      scores: t.scores,
      at: t.amended_at,
      by: t.amended_by,
    },
  ];
  return all.map((h, i) => {
    const now = i === all.length - 1;
    const meta = [nameOf(h.by) ? `by ${nameOf(h.by)}` : null, when(h.at)]
      .filter(Boolean)
      .join(" · ");
    return {
      n: i + 1,
      label: `Result ${i + 1}${now ? " (now)" : ""}`,
      now,
      output: h.output ?? null,
      status: h.status ?? null,
      error: h.error ?? null,
      scores: scoresText(h.scores),
      meta,
    };
  });
}

/** `History · 3 results`; null when the cell was never re-run. */
export function historyButtonText(t) {
  const n = Array.isArray(t?.history) ? t.history.length : 0;
  return n ? `History · ${n + 1} results` : null;
}

/** One activity line per amendment of a run, newest first. */
export function amendmentLines(amendments = []) {
  return [...(amendments ?? [])]
    .sort((a, b) => String(b?.at ?? "").localeCompare(String(a?.at ?? "")))
    .map((a) =>
      [
        `${a?.summary ?? "Amended"}${nameOf(a?.by) ? ` by ${nameOf(a.by)}` : ""}`,
        when(a?.at),
      ]
        .filter(Boolean)
        .join(" · "),
    );
}
