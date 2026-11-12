// Side by side · data + derived view model. Fetches the dataset's runs (for
// the selection + pinned baseline), the matrix over the selection (rail),
// the task payload for the lanes and the review state; derives lane views
// (trial, lane steps, colours, verdict, blind names), the alignment, markers,
// story rows and the why line. Pure derivations live in the *Model.js files.
import { useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "@mui/material";
import { useDatasetRuns } from "src/api/develop/runs";
import { useMatrix } from "src/api/develop/r2";
import { useTaskCompare, useTaskReview } from "src/api/develop/r3";
import { runColorAt, useRunColors } from "src/sections/common/Runs/runColors";
import {
  alignLanes,
  firstDifferenceText,
  firstWrongStepText,
  markers as markersOf,
  toLaneSteps,
} from "src/sections/common/Align/milestones";
import {
  MAX_LANES,
  blindOrder,
  defaultLaneIds,
  firstWorseRow,
  isSessionId,
  laneVerdict,
  openPairIds,
  orderLanes,
  railFilterOf,
  railRunIds,
  railSets,
  resolveBaseline,
  selectionOf,
  trialOfLane,
} from "./workspaceModel";
import { whySentence } from "./whyModel";
import { trialHasTrace } from "src/sections/common/Trace/traceChipModel";
import { laneNames } from "./storyGrid";
import { fromParams } from "./useTaskCompareState";

const KIND_WORD = {
  prompt: "prompt",
  agent: "agent",
  code: "code",
  harness: "harness",
  model: "model",
  production: "production",
};
const runOrder = (r) => [
  Number(r.experiment_ordinal) || 0,
  r.letter ?? r.label ?? "",
];

/**
 * TC-02: the matrix's run set in one order, so the screen's two phases
 * (before the pair is picked and after) share ONE request (pure). The
 * runs the page compares stay first (the matrix answers a capped number
 * of runs): the baseline, then the link's runs and the lanes (the lanes
 * are picked from the link's runs) by id, then the rest of the selection
 * by id; no repeats.
 * @param {string[]} lanes
 * @param {string[]} [others]  the rest of the selection
 * @param {string|null} [baselineId]
 * @param {string[]} [linkIds]  the runs named in the link (`runs=`)
 */
export function canonicalRunIds(
  lanes,
  others = [],
  baselineId = null,
  linkIds = [],
) {
  const primary = [
    ...new Set([...(lanes ?? []), ...(linkIds ?? [])].filter(Boolean)),
  ];
  const all = new Set([...primary, ...(others ?? []).filter(Boolean)]);
  const head = all.has(baselineId) ? [baselineId] : [];
  const mid = primary.filter((id) => id !== baselineId).sort();
  const seen = new Set([...head, ...mid]);
  const tail = [...all].filter((id) => !seen.has(id)).sort();
  return [...head, ...mid, ...tail];
}

export default function useWorkspaceData({ datasetId, st, maxLanes, user }) {
  const theme = useTheme();
  const { params: p, rail, view } = st;
  const runsQ = useDatasetRuns(datasetId, { versionId: "all" });
  const datasetRuns = useMemo(
    () =>
      [...(runsQ.data?.results ?? [])].sort((a, b) => {
        const [ea, la] = runOrder(a);
        const [eb, lb] = runOrder(b);
        return ea - eb || String(la).localeCompare(String(lb));
      }),
    [runsQ.data],
  );
  const runById = useMemo(
    () => new Map(datasetRuns.map((r) => [r.run_id, r])),
    [datasetRuns],
  );
  const pinnedId = runsQ.data?.baseline?.run_id ?? null;
  const urlIds = p.runIds;
  // IT-R5 G4 P4: more runs than lanes opens on a PAIR (`baseline` + `cand`,
  // else the 2 most different), never on a card picker; the other runs
  // stay one click away (the strip above the lanes, Lanes).
  const overLimit = urlIds.length > MAX_LANES;
  const candId = st.sp?.get?.("cand") ?? null;
  const urlKey = urlIds.join(",");
  const [autoPair, setAutoPair] = useState(null);
  const candPair = overLimit
    ? openPairIds({ urlIds, baselineId: p.baselineId ?? pinnedId, candId })
    : null;
  const pairIds =
    candPair && candId
      ? candPair
      : autoPair?.key === urlKey
        ? autoPair.ids
        : null;
  const cards = overLimit && !pairIds;

  const fromRuns = useMemo(
    () =>
      (fromParams(p.from)?.get("runs") ?? "")
        .split(",")
        .filter((id) => runById.has(id)),
    [p.from, runById],
  );
  // A link that names only the task (⌘K `T26`, a shared `?row=` link)
  // compares the pinned baseline's experiment; the lanes are filled in
  // below once the matrix row is known.
  const autoLanes = urlIds.length === 0 && fromRuns.length === 0;
  const selection = useMemo(() => {
    const reqRuns = urlIds.map((id) => runById.get(id)).filter(Boolean);
    const pinned = autoLanes ? runById.get(pinnedId) : null;
    const ids = selectionOf({
      lanes: pinned ? [pinned] : reqRuns,
      fromRuns,
      datasetRuns,
    });
    const pos = new Map(datasetRuns.map((r, i) => [r.run_id, i]));
    return ids
      .filter((id) => pos.has(id))
      .sort((a, b) => pos.get(a) - pos.get(b));
  }, [urlIds, runById, fromRuns, datasetRuns, autoLanes, pinnedId]);
  const baselineId = resolveBaseline(
    urlIds.length ? urlIds : selection,
    p.baselineId,
    pinnedId,
  );
  const pairKey = pairIds ? pairIds.join(",") : "";
  const laneIds = useMemo(
    () =>
      cards
        ? []
        : pairIds
          ? orderLanes(pairIds, baselineId)
          : orderLanes(urlIds, baselineId),
    [cards, urlIds, baselineId, pairKey], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // The lanes go first: the matrix answers at most 8 runs, and the rail
  // counts over the lanes on screen (#3E + #2E were cut off → `Differ (0)`).
  // TC-02 (options-truth L3 G10): one request for the screen — the run set
  // in one canonical order (baseline first, then by id), so the URL's order
  // and the lanes' order share a cache entry instead of fetching twice.
  const matrixIds = useMemo(
    () =>
      canonicalRunIds(
        laneIds.filter((id) => !isSessionId(id)),
        selection,
        baselineId,
        urlIds.filter((id) => !isSessionId(id)),
      ),
    [laneIds, selection, baselineId, urlIds],
  );
  const matrixQ = useMatrix(
    { datasetId, runIds: matrixIds, baselineId },
    { enabled: selection.length > 0 },
  );
  const matrix = matrixQ.data;
  // The rail counts over the runs on screen, not the experiment's siblings.
  const railIds = useMemo(
    () => railRunIds({ cards, urlIds, laneIds, selection }),
    [cards, urlIds, laneIds, selection],
  );

  // Row: the URL's, else the first task of the rail filter.
  const reviewRunIds = cards ? urlIds : laneIds;
  const focusId = st.sp?.get("focus") ?? null;
  const sets0 = useMemo(
    () =>
      matrix
        ? railSets(matrix, { runIds: railIds, baselineId, focusId })
        : null,
    [matrix, railIds, baselineId, focusId],
  );
  // X's `Why?` link (`rail=worse&focus=<run>`) without `runs=`: the
  // baseline and the focused run are the lanes.
  useEffect(() => {
    if (focusId && !urlIds.length && baselineId && focusId !== baselineId)
      st.set({ runIds: [baselineId, focusId], baselineId });
  }, [focusId, baselineId]); // eslint-disable-line react-hooks/exhaustive-deps
  // IT-R5 G4 P4: with no row in the link, the first task where the
  // candidate is worse than the baseline, else the first of the rail.
  const rowId =
    p.rowId ??
    (laneIds.length >= 2
      ? firstWorseRow(matrix, sets0?.[rail] ?? [], laneIds[0], laneIds[1])
      : null) ??
    sets0?.[rail]?.[0] ??
    sets0?.all?.[0] ??
    null;
  useEffect(() => {
    if (!p.rowId && rowId) st.set({ rowId });
  }, [p.rowId, rowId]); // eslint-disable-line react-hooks/exhaustive-deps

  const reviewQ = useTaskReview(
    { datasetId, rowId, runIds: reviewRunIds },
    { enabled: Boolean(rowId) },
  );
  const rowsState = useMemo(() => reviewQ.data?.rows ?? {}, [reviewQ.data]);
  const sets = useMemo(
    () =>
      matrix
        ? railSets(matrix, { runIds: railIds, baselineId, rowsState, focusId })
        : null,
    [matrix, railIds, baselineId, rowsState, focusId],
  );

  // IT-R5 G4 P4 (rule 39): an explicit `row=` is always honoured; when the
  // rail filter would hide it, the rail widens to All tasks and says so.
  // With no differences, the rail shows every task and says that instead.
  // Once per link (a later pick of a filter is the user's).
  const [railNote, setRailNote] = useState(null);
  const widened = useRef(null);
  useEffect(() => {
    if (!sets0 || widened.current === urlKey) return;
    widened.current = urlKey;
    if (rail === "all") return;
    const inRail = sets0[rail] ?? [];
    if (p.rowId && !inRail.includes(p.rowId) && sets0.all.includes(p.rowId)) {
      const label =
        matrix?.rows?.find((r) => r.row_id === p.rowId)?.label ?? "This task";
      const long = railFilterOf(rail, { baselineId })?.long ?? "this list";
      setRailNote({
        rowId: p.rowId,
        text: `${label} is not in "${long}" · showing all`,
      });
      st.set({ rail: "all" });
    } else if (rail === "differ" && !inRail.length && sets0.all.length) {
      setRailNote({
        rowId: null,
        text: "These runs give the same result on every task",
      });
      st.set({ rail: "all" });
    }
  }, [sets0, urlKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const tcQ = useTaskCompare(
    { datasetId, rowId, runIds: cards ? urlIds : laneIds },
    { enabled: Boolean(rowId) && (cards || laneIds.length > 0) },
  );
  const data = tcQ.data;

  const colorIds = useMemo(
    () => [
      ...new Set([
        ...selection,
        ...laneIds.filter((id) => !isSessionId(id)),
        ...(pairIds ? urlIds : []),
      ]),
    ],
    [selection, laneIds, pairKey], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const colors = useRunColors(colorIds, baselineId);
  const mode = theme.palette.mode === "dark" ? "dark" : "light";
  const blind = p.blind && !cards;
  const blindInfo = useMemo(
    () => blindOrder(laneIds, `${user}|${rowId}`),
    [laneIds, user, rowId],
  );
  const grey = {
    solid: theme.palette.grey[500],
    tint: theme.palette.action.hover,
    fg: theme.palette.text.primary,
  };

  const lanes = useMemo(() => {
    const list = (data?.lanes ?? []).map((lane) => {
      const trial = trialOfLane(lane, p.trials);
      const steps = toLaneSteps(trial?.steps ?? [], {
        runKind: lane.kind === "production" ? "agent" : lane.kind,
      });
      const raw = trial?.steps ?? [];
      const byN = new Map(raw.map((s) => [s.n, s]));
      const kindByN = new Map(steps.map((s) => [s.n, s.kind]));
      const color = blind
        ? grey
        : colors[lane.run_id] ??
          runColorAt(
            isSessionId(lane.run_id) ? 7 : lane.color_index ?? 0,
            mode,
          );
      return {
        ...lane,
        id: lane.run_id,
        color,
        verdict: laneVerdict(lane),
        trial,
        steps,
        rawSteps: raw,
        checks: trial?.checks ?? [],
        story: trial?.story ?? [],
        blindName: blind ? blindInfo.names[lane.run_id] : null,
        promptOf: (n) => {
          const s = byN.get(n);
          if (!s) return null;
          return (
            s.prompt ??
            (Array.isArray(s.input)
              ? s.input.find((m) => m.role === "system")?.content ?? null
              : null)
          );
        },
        stepKindOf: (n) => kindByN.get(n),
        modelOf: (n) => byN.get(n)?.model ?? null,
        // One predicate for Why, Steps, Graph and Timeline (IT-R1 G4).
        noTrace: !trialHasTrace(trial),
        noTraceReason:
          lane.kind === "production"
            ? null
            : trial?.no_trace_reason ??
              lane.not_run_reason ??
              "No trace was recorded for this trial",
      };
    });
    if (!blind) return list;
    const at = new Map(blindInfo.order.map((id, i) => [id, i]));
    return [...list].sort((a, b) => (at.get(a.id) ?? 0) - (at.get(b.id) ?? 0));
  }, [data, p.trials, blind, blindInfo, colors, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // Human names (`Support Agent v3`) in every derived line, not run codes.
  const humanNames = useMemo(() => laneNames(lanes), [lanes]);
  const nameOf = (l) => humanNames.get(l.id) ?? l.blindName ?? l.label;
  // Lanes without a trace for the picked trial (setup failed, carried, not
  // run on this dataset version) sit out of the alignment; their columns
  // stay (empty) so every grid keeps one column per lane.
  const tIdx = useMemo(
    () => lanes.map((l, i) => (l.noTrace ? -1 : i)).filter((i) => i >= 0),
    [lanes],
  );
  const traced = useMemo(() => tIdx.map((i) => lanes[i]), [tIdx, lanes]);
  const raw = useMemo(() => {
    if (!traced.length) return null;
    const lane = (l) => ({ id: l.id, label: nameOf(l), steps: l.steps });
    const m = view.align === "index" ? "index" : "milestone";
    return alignLanes(lane(traced[0]), traced.slice(1).map(lane), { mode: m });
  }, [traced, view.align]); // eslint-disable-line react-hooks/exhaustive-deps
  const align = useMemo(() => {
    if (!raw) return null;
    if (tIdx.length === lanes.length) return raw;
    return {
      ...raw,
      rows: raw.rows.map((r) => ({
        ...r,
        cells: lanes.map((_, i) => {
          const k = tIdx.indexOf(i);
          return k >= 0 ? r.cells[k] : null;
        }),
      })),
    };
  }, [raw, tIdx, lanes]);

  const marks = useMemo(() => {
    if (!raw) return { firstWrongStep: null, firstDifference: null };
    return markersOf({
      rows: raw.rows,
      lanes: traced.map((l) => ({
        id: l.id,
        label: nameOf(l),
        steps: l.steps,
      })),
      checks: Object.fromEntries(traced.map((l) => [l.id, l.checks])),
    });
  }, [raw, traced]); // eslint-disable-line react-hooks/exhaustive-deps

  const why = useMemo(
    () =>
      raw
        ? whySentence({
            rows: raw.rows,
            lanes: traced.map((l) => ({
              id: l.id,
              label: nameOf(l),
              steps: l.steps,
              checks: l.checks,
              verdict: l.verdict,
            })),
            markers: marks,
          })
        : null,
    [raw, traced, marks], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const kinds = [...new Set(lanes.map((l) => l.kind))];
  const mixedKinds =
    kinds.length > 1 && !blind
      ? kinds.map((k) => KIND_WORD[k] ?? k).join(", ")
      : null;

  const diffRows = useMemo(
    () =>
      (align?.rows ?? [])
        .filter((r) => r.kind === "milestone" && r.change !== "same")
        .map((r) => r.id),
    [align],
  );
  const anchorRowId = useMemo(() => {
    if (!p.step || !align) return null;
    if (p.step.startsWith("m"))
      return align.rows.some((r) => r.id === p.step) ? p.step : null;
    const n = Number(p.step.slice(1));
    const row = align.rows.find(
      (r) =>
        r.cells[0]?.type === "step" &&
        (r.cells[0].step?.n === n ||
          r.cells[0].subSteps?.some((s) => s.n === n)),
    );
    return row?.id ?? null;
  }, [p.step, align]);

  const mrow = matrix?.rows?.find((r) => r.row_id === rowId) ?? null;
  const defaultIds = useMemo(
    () =>
      mrow
        ? defaultLaneIds({
            selection: cards ? urlIds : selection,
            baselineId,
            cells: mrow.cells,
            maxLanes,
          })
        : [],
    [mrow, cards, urlIds, selection, baselineId, maxLanes],
  );
  useEffect(() => {
    if (autoLanes && defaultIds.length > 1)
      st.set({ runIds: defaultIds, baselineId });
  }, [autoLanes, defaultIds, baselineId]); // eslint-disable-line react-hooks/exhaustive-deps
  // IT-R5 G4 P4: no `cand` → the 2 most different runs on this task.
  useEffect(() => {
    if (!overLimit || (candPair && candId) || autoPair?.key === urlKey) return;
    if (!mrow) return;
    const ids = openPairIds({
      urlIds,
      baselineId,
      cells: mrow.cells,
      order: datasetRuns.map((r) => r.run_id),
    });
    if (ids?.length === 2) setAutoPair({ key: urlKey, ids });
  }, [overLimit, urlKey, mrow, baselineId]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    runsQ,
    matrixQ,
    tcQ,
    reviewQ,
    datasetRuns,
    runById,
    pinnedId,
    baselineId,
    laneIds,
    cards,
    // IT-R5 G4 P4: the link's runs when the lanes are a pair of them.
    pairOf: pairIds ? urlIds : null,
    pairRuns: pairIds
      ? urlIds
          .map((id) => runById.get(id))
          .filter(Boolean)
          .map((r) => ({ ...r, color: blind ? grey : colors[r.run_id] }))
      : [],
    // The pair waits on the matrix row (a skeleton, never a card flash).
    pairPending:
      cards && !matrixQ.isError && (matrixQ.isLoading || Boolean(mrow)),
    // The widen note stays while its row is open (any row for `same`).
    railNote:
      st.rail === "all" &&
      railNote &&
      (!railNote.rowId || railNote.rowId === p.rowId)
        ? railNote.text
        : null,
    selection,
    selectionRuns: selection.map((id) => ({
      ...runById.get(id),
      color: blind ? grey : colors[id],
    })),
    matrix,
    sets,
    rowsState,
    rowId,
    mrow,
    data,
    row: data?.row ?? null,
    lanes,
    align,
    marks,
    why,
    firstWrongText: firstWrongStepText(marks.firstWrongStep),
    firstDiffText: firstDifferenceText(marks.firstDifference),
    mixedKinds,
    diffRows,
    anchorRowId,
    defaultIds,
    blind,
    blindInfo,
    review: reviewQ.data ?? null,
  };
}
