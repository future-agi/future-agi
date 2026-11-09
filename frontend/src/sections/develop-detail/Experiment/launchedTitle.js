// The launched drawer's title and helper once every waiting run is in
// (IT-R1 G1 P5 `Results in · 24/24`). options-truth L3 G3
// (knob:limits:cost-cap, made by G8): a run that stopped at its cost cap is
// not "every row is in": `Stopped at cost cap $0.80 · 26 of 40 tries` and
// `2 tasks were not run. Resume them or raise the cap.` W1-6 (review M4):
// when R1 sends each run's ledger, the title prints the one ledger
// (`Results in · 16 rows scored · 4 skipped (no expected value)`), and the
// counts above are only the fallback for a backend without one. Pure.
import {
  STOP_WORDS,
  ledgerParts,
  mergeLedgers,
} from "src/sections/common/Runs/runLedger";

const sum = (xs, f) => xs.reduce((n, x) => n + (Number(f(x)) || 0), 0);

/** The title and helper from the runs' ledgers (one per run in). */
function fromLedgers(all, ledgers, rowNoun) {
  const merged = mergeLedgers(ledgers);
  const runs = ledgers.length > 1 ? `${ledgers.length} runs · ` : "";
  const stopped = all.filter((r) => /^Stopped/.test(r.stop_line ?? ""));
  if (!stopped.length && !merged.stopped_by) {
    const line = ledgerParts(merged, { noun: rowNoun })
      .map((p) => p.text)
      .join(" · ");
    return {
      title: `Results in · ${runs}${line}`,
      helper: null,
      stopped: false,
    };
  }
  const reasons = [
    ...new Set(stopped.map((r) => String(r.stop_line).split(" · ")[0])),
  ];
  const words = STOP_WORDS[merged.stopped_by] ?? "stopped";
  const head =
    reasons.length === 1
      ? reasons[0]
      : reasons.length > 1
        ? "Stopped at the run caps"
        : `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
  const line = ledgerParts(merged, { noun: rowNoun, stopWords: false })
    .map((p) => p.text)
    .join(" · ");
  const notRun = merged.rows.stopped;
  const why = /cost cap/i.test(head) ? "raise the cap" : "raise the limit";
  return {
    title: head,
    helper: notRun
      ? `${runs}${line}. Resume ${notRun === 1 ? "it" : "them"} or ${why}.`
      : `${runs}${line}. Every ${rowNoun} ran before the stop.`,
    stopped: true,
  };
}

/**
 * @param {Array<{done: number, total?: number, stop_line?: string|null,
 *   rows_done?: number|null, rows_total?: number|null}>} resultsIn
 *   one entry per waiting run (`launched.resultsIn`)
 * @param {{rowNoun?: "task"|"row", ledgers?: object[]|null}} [o]
 *   `ledgers`: each waiting run's R1 ledger (all of them, or the counts
 *   above are used)
 * @returns {{title: string, helper: string|null, stopped: boolean}}
 */
export function launchedTitleOf(
  resultsIn,
  { rowNoun = "row", ledgers = null } = {},
) {
  const all = resultsIn ?? [];
  const known = (ledgers ?? []).filter((l) => l?.rows);
  if (known.length && known.length === (ledgers ?? []).length)
    return fromLedgers(all, known, rowNoun);
  const done = sum(all, (r) => r.done);
  const total = sum(all, (r) => r.total ?? r.done);
  const stopped = all.filter((r) => /^Stopped/.test(r.stop_line ?? ""));
  if (!stopped.length)
    return {
      title: `Results in · ${done}/${total}`,
      helper: null,
      stopped: false,
    };
  const reasons = [
    ...new Set(stopped.map((r) => String(r.stop_line).split(" · ")[0])),
  ];
  const head = reasons.length === 1 ? reasons[0] : "Stopped at the run caps";
  const notRun = sum(stopped, (r) =>
    r.rows_total != null && r.rows_done != null
      ? Math.max(0, r.rows_total - r.rows_done)
      : 0,
  );
  const noun = notRun === 1 ? rowNoun : `${rowNoun}s`;
  const why = /cost cap/i.test(head) ? "raise the cap" : "raise the limit";
  return {
    title: `${head} · ${done} of ${total} tries`,
    helper: notRun
      ? `${notRun} ${noun} ${notRun === 1 ? "was" : "were"} not run. Resume ${notRun === 1 ? "it" : "them"} or ${why}.`
      : `Every ${rowNoun} ran before the stop. Open the experiment to see what passed and what failed.`,
    stopped: true,
  };
}
