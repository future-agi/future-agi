// MUST 5: one-click voice filters for the results grid on
// scenarios datasets. Each is a `rows` pill over the rows where any run in
// view matches, so the count shown is the number of rows it keeps.
import { rowsPill } from "src/sections/common/Results/filterPills";
import { fmtMs } from "src/sections/common/format/fmt";

/** Latency p90 above this (ms) is slow for a voice turn. */
export const SLOW_P90_MS = 2000;

export const VOICE_FILTERS = Object.freeze([
  { id: "has_audio", label: "Has audio", test: (c) => c.hasAudio },
  {
    id: "slow_p90",
    label: `Latency p90 > ${fmtMs(SLOW_P90_MS)}`,
    test: (c) => Number.isFinite(c.p90) && c.p90 > SLOW_P90_MS,
  },
  {
    id: "kept_talking",
    label: "Kept talking over the caller ≥ 1",
    test: (c) => (c.kept ?? 0) >= 1,
  },
  {
    id: "failed_tool",
    label: "Failed on a tool call",
    test: (c) => c.failedTool,
  },
  {
    id: "recording_missing",
    label: "Recording missing",
    test: (c) => c.recordingMissing,
  },
]);

/**
 * @param {{[rowId]: {[runId]: object}}} calls callTable(...).calls
 * @param {{runIds?: string[]}} opts runs in view (all when omitted)
 * @returns {{id, label, count, ids, pill}[]} filters with at least one row
 */
export function voiceQuickFilters(calls, { runIds } = {}) {
  if (!calls) return [];
  return VOICE_FILTERS.map((f) => {
    const ids = Object.entries(calls)
      .filter(([, byRun]) =>
        Object.entries(byRun).some(
          ([runId, c]) => (!runIds || runIds.includes(runId)) && c && f.test(c),
        ),
      )
      .map(([rowId]) => rowId);
    return {
      id: f.id,
      label: f.label,
      count: ids.length,
      ids,
      pill: rowsPill(ids, { label: f.label }),
    };
  }).filter((f) => f.count > 0);
}
