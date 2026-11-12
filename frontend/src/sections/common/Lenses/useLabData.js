// LensLab data (prototype page): one real row and its runs' results through
// the existing hooks, shaped as ModalityLens lanes. Not used by product
// screens (Task compare builds its own lanes).
import { useMemo } from "react";
import { useDatasetRuns } from "src/api/develop/runs";
import { useResultRow } from "src/api/develop/run-results";
import { useRunColors } from "src/sections/common/Runs/runColors";

const DS = (d) => `d5a1e7c2-0b3f-4a8d-9e61-1c2b3a4d5e0${d}`;
const RUN = (d, ord, letter) =>
  `a${d}000000-0000-4000-8000-${String(ord).padStart(11, "0")}${letter}`;
const ROW = (d, n) =>
  `b${d}000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

/** Lens defaults (MUST 7). */
export const LAB_DEFAULTS = Object.freeze({
  call: {
    dataset: DS(9),
    row: ROW(9, 3),
    runs: [RUN(9, 1, "a"), RUN(9, 1, "b")],
    note: "VX #1 · S1 · Mike · #1A vs #1B",
  },
  image: {
    dataset: DS(8),
    row: ROW(8, 4),
    runs: [RUN(8, 2, "a"), RUN(8, 2, "b")],
    note: "MM #2 · row 4 · #2A vs #2B",
  },
  document: {
    dataset: DS(8),
    row: ROW(8, 23),
    runs: [RUN(8, 1, "a"), RUN(8, 1, "b")],
    note: "MM #1 · contract row 23 · #1A vs #1B",
  },
  json: {
    dataset: DS(8),
    row: ROW(8, 23),
    runs: [RUN(8, 1, "a"), RUN(8, 1, "b")],
    note: "MM #1 · contract row 23 · #1A vs #1B",
  },
  audio: {
    dataset: DS(9),
    row: ROW(9, 1),
    runs: [RUN(9, 2, "a"), RUN(9, 2, "b")],
    note: "VX #2 · row 1 · #2A vs #2B",
  },
  video: {
    dataset: DS(8),
    row: ROW(8, 1),
    runs: [RUN(8, 1, "a"), RUN(8, 1, "b")],
    note: "MM · row 1 input clip (no run in the world outputs video)",
  },
});

export function labParams(search, lens) {
  const d = LAB_DEFAULTS[lens] ?? LAB_DEFAULTS.call;
  const runs = (search.get("runs") ?? "").split(",").filter(Boolean);
  const custom = search.get("row") || runs.length || search.get("dataset");
  return {
    datasetId: search.get("dataset") || d.dataset,
    rowId: search.get("row") || d.row,
    runIds: runs.length ? runs : d.runs,
    note: custom ? "the row and runs in the URL" : d.note,
  };
}

/** The labelled box of an image row (`Box overlap` eval's measured data). */
const labelledBoxes = (result) => {
  const m = Object.values(result?.scores ?? {}).find(
    (x) => x?.measured?.expected_box,
  )?.measured;
  if (!m) return undefined;
  const [x, y, w, h] = m.expected_box;
  return [{ x, y, w, h, kind: "expected", label: "Labelled" }];
};

const outputOf = (result) => {
  if (!result) return null;
  return result.output ?? result.trials_detail?.[0]?.output ?? null;
};

/**
 * @returns {{lanes: object[], row: object|null, runsById: object,
 *   isLoading: boolean, isError: boolean, refetch: Function}}
 */
export default function useLabData({ datasetId, rowId, runIds }) {
  const runsQ = useDatasetRuns(datasetId);
  const rowQ = useResultRow(
    datasetId,
    { runIds, baselineId: runIds[0] },
    rowId,
  );
  const colors = useRunColors(runIds, runIds[0]);
  const runsById = useMemo(
    () =>
      Object.fromEntries((runsQ.data?.results ?? []).map((r) => [r.run_id, r])),
    [runsQ.data],
  );
  const row = rowQ.data ?? null;
  const lanes = useMemo(
    () =>
      runIds.map((id, i) => {
        const run = runsById[id];
        const result = row?.results?.[id];
        return {
          runId: id,
          label: run ? [run.label, run.name].filter(Boolean).join(" ") : id,
          color: colors[id]?.solid,
          isBaseline: i === 0,
          value: outputOf(result),
          trace_id: result?.trace_ids?.[0] ?? undefined,
          boxes: labelledBoxes(result),
          passed:
            result?.trial_outcomes?.[0] == null
              ? undefined
              : Boolean(result.trial_outcomes[0]),
          result,
        };
      }),
    [runIds, runsById, row, colors],
  );
  return {
    lanes,
    row,
    runsById,
    isLoading: rowQ.isLoading || runsQ.isLoading,
    isError: rowQ.isError,
    refetch: rowQ.refetch,
  };
}
