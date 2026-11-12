import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Popover,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import { useLocation, useNavigate } from "react-router-dom";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useRunColors } from "src/sections/common/Runs/runColors";
import RunPickerPopover from "src/sections/common/Runs/RunPickerPopover";
import { configKeyOf, useRunConfigs } from "src/api/develop/r2";
import { versionTags as versionTagsOf } from "../results/pageCandidate";
import { useDatasetRuns } from "src/api/develop/runs";
import {
  byDatasetHref,
  datasetGridHref,
  iterateHref,
  matrixHref,
  taskCompareHref,
} from "src/sections/common/Analysis/analysisLinks";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";
import useViewportDensity from "src/sections/common/ux/oneview/useViewportDensity";
import Section from "src/sections/common/ux/oneview/Section";
import {
  coverageText,
  scopeVerdictInputs,
  scopedVerdict,
  shipLineOf,
  verdictsAsText,
} from "./verdictScope";
import { copyText } from "src/sections/common/ux/CopyButton";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import notify from "src/sections/common/ux/toast";
import { fmtCount, fmtNumber } from "src/sections/common/format/fmt";
import {
  rowsPill,
  rowIdsFromPills,
} from "src/sections/common/Results/filterPills";
import { rowIdsFilter } from "src/sections/develop-detail/DataTab/DataGrid/dataGridView";
import ResultDrawer from "../results/ResultDrawer";
import useResultsUrlState, {
  MAX_COMPARE_RUNS,
} from "../results/useResultsUrlState";
import {
  autoPair,
  buildMatrixModel,
  GROUP_OPTIONS,
  matrixStatusText,
  matrixTries,
  paretoModel,
  verdictInputs,
  versionsBanner,
} from "./matrixModel";
import useMatrixData from "./useMatrixData";
import { comparableRunIds, leftOutLine, otherGroupIds } from "./comparableRuns";
import {
  MIXED_RUNS_WORDS,
  isMixedRunsRefusal,
} from "src/utils/mixedRunsRefusal";
import useVoiceMatrix, {
  asVerdictLine,
  heatOf,
  heatOptionsFor,
} from "./useVoiceMatrix";
import { heatLegend } from "src/sections/common/Analysis/voiceSummaryModel";
import {
  bestValueOf,
  bestValueText,
  runsOfMatrix,
} from "src/sections/common/Runs/runNumbers";
import { runShortName } from "src/sections/common/Runs/runLabel";
import { VOCAB } from "src/sections/common/vocab";
import { openRerunWithChanges } from "src/sections/common/RunSources/rerunBus";
import { MatrixToolbar } from "./MatrixHeader";
import MatrixGrid, { BottomLine, CalloutAction, cellDomId } from "./MatrixGrid";
import MatrixHoverCard from "./MatrixHoverCard";
import PatternCallout from "./PatternCallout";
import ParetoStrip, { frontierLine, paretoPlottable } from "./ParetoStrip";
import VerdictFold from "../results/VerdictFold";
import { pageCandidateId, whoText } from "./pageShip";
import { LegendHelp } from "./MatrixLegend";
import { explainCoverage, whoFailsText } from "./explainCoverage";
import { canRerunHere } from "src/sections/common/Runs/runKind";
import { mergeLedgers } from "src/sections/common/Runs/runLedger";
import { canIterate } from "../iterate/iterateModel";
import useMatrixRunActions from "src/sections/common/MatrixRuns/MatrixRunMenu";
import { RULES_METHOD_LABEL } from "src/sections/experiment-detail/results/causeMethod";

const PREFS_KEY = "fa.matrix.prefs";
const LAST_PAIR_KEY = "fa.matrix.lastPair";
const readLastPair = () => {
  try {
    const v = JSON.parse(window.localStorage.getItem(LAST_PAIR_KEY) ?? "null");
    return Array.isArray(v) ? v : null;
  } catch {
    return null;
  }
};
const readPrefs = () => {
  try {
    return JSON.parse(window.localStorage.getItem(PREFS_KEY) ?? "{}") ?? {};
  } catch {
    return {};
  }
};
const writePrefs = (p) => {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* per-viewer convenience only */
  }
};

const HOVER_DELAY = 300;
const MAX_LABELS = 8;
/** `T3, T5, T7` or `Row 1, Row 2, … +150 more` (the action opens all of them). */
const labelList = (rows) =>
  rows.length > MAX_LABELS
    ? `${rows
        .slice(0, MAX_LABELS)
        .map((r) => r.label)
        .join(", ")} +${fmtNumber(rows.length - MAX_LABELS)} more`
    : rows.map((r) => r.label).join(", ");
const DEVELOP = "/dashboard/develop";
const NOT_IN_RELEASE = "Not available in this release";
const baseLabelOf = (model, runsById) =>
  runsById[model?.baselineId]?.label ??
  model?.columnsById?.[model?.baselineId]?.label ??
  "the baseline";

function Notice({
  children,
  tone = "info",
  icon = "mdi:information-outline",
  action,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={(t) => ({
        mx: 1,
        px: 1.25,
        py: 0.75,
        borderRadius: 0.5,
        border: "1px solid",
        borderColor: alpha(t.palette[tone].main, 0.3),
        bgcolor: alpha(t.palette[tone].main, 0.06),
      })}
    >
      <Iconify
        icon={icon}
        width={18}
        sx={{ color: `${tone}.main`, flexShrink: 0 }}
      />
      <Typography typography="s2" sx={{ flex: 1 }}>
        {children}
      </Typography>
      {action}
    </Stack>
  );
}

Notice.propTypes = {
  children: PropTypes.node,
  tone: PropTypes.string,
  icon: PropTypes.string,
  action: PropTypes.node,
};

function LoadingGrid() {
  return (
    <Box sx={{ px: 1 }} data-testid="matrix-loading">
      <Stack direction="row" gap={0.5} sx={{ mb: 0.5 }}>
        <Skeleton variant="rounded" width={260} height={120} />
        {[0, 1, 2, 3, 4].map((c) => (
          <Skeleton key={c} variant="rounded" width={132} height={120} />
        ))}
      </Stack>
      {Array.from({ length: 12 }, (_, r) => (
        <Stack key={r} direction="row" gap={0.5} sx={{ mb: 0.5 }}>
          <Skeleton variant="rounded" width={260} height={40} />
          {[0, 1, 2, 3, 4].map((c) => (
            <Skeleton key={c} variant="rounded" width={132} height={40} />
          ))}
        </Stack>
      ))}
    </Box>
  );
}

/**
 * Matrix tab body (R2-SPEC P1 §3.3.1): rows × runs × trials on one screen,
 * with the PatternCallout, the Pareto strip, sticky headers and bottom
 * rows; a cell opens the result drawer on the trace pair.
 *
 * Props (frozen, R2 manifest row 4):
 * @param {"experiment"|"compare"} props.mode
 * @param {string} props.datasetId
 * @param {string} [props.experimentId]  experiment mode only
 * @param {string[]} props.runIds  runs in view, baseline first
 * @param {string|null} props.baselineId
 */
const MatrixView = ({
  mode,
  datasetId,
  experimentId,
  runIds: runIdsProp,
  baselineId: baselineProp,
}) => {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const url = useResultsUrlState();
  const { patch } = url;
  const sp = new URLSearchParams(search);
  // One view (A3-15/16/17): More metrics, cost bars, density and the
  // Frontier chart are the viewer's (layout `matrix`, remembered); the
  // defaults follow the window (short 1280×720: More metrics off, chart
  // folded). `more=` / `chart=` (1/0) in a link still win.
  const viewport = useViewportDensity();
  const [layout, patchLayout] = useLayoutPrefs("matrix");
  const ex = layout.extras ?? {};
  const moreParam = sp.get("more");
  const moreMetrics =
    moreParam != null
      ? moreParam === "1"
      : ex.moreMetrics ?? viewport !== "short";
  const chartParam = sp.get("chart");
  // R3 MUST 3: on the experiment page the default baseline is the dataset's
  // pinned baseline when it is one of the experiment's runs.
  const { data: datasetRuns, isLoading: datasetRunsLoading } =
    useDatasetRuns(datasetId);
  // IT-R4 G6 P1: the Experiments headline's runs (with success rates), so
  // a page without `cand=` picks the same candidate (G2 pickHeadline).
  const { data: headlineRuns } = useDatasetRuns(datasetId, {
    include: "metrics,config",
    refetchOnWindowFocus: false,
  });
  const pinnedId = datasetRuns?.pinned_run_id ?? null;
  const baselineId =
    mode === "experiment" &&
    !url.baseline &&
    pinnedId &&
    (runIdsProp ?? []).includes(pinnedId)
      ? pinnedId
      : baselineProp;
  const { role } = useAuthContext();
  const readOnly = !RolePermission.DATASETS[PERMISSIONS.UPDATE][role];
  const runIdsKey = (runIdsProp ?? []).join(",");
  // The runs asked for (the URL's on the compare page): Add run, the lane ×
  // and its Undo keep them all.
  const requestedIds = useMemo(() => runIdsProp ?? [], [runIdsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  // The read lines up only runs that share one dataset version, trial plan
  // and evaluators (R1 `comparison_group`); any other mix is refused. The
  // compare page reads the baseline's group and names the runs it leaves
  // out (the experiment page passes its newest group already). It waits
  // for R1 so a mixed read is not sent first.
  const lineUp = useMemo(
    () =>
      mode === "compare"
        ? comparableRunIds(requestedIds, {
            baselineId,
            runs: datasetRuns?.results,
          })
        : { runIds: requestedIds, left: [] },
    [mode, requestedIds, baselineId, datasetRuns],
  );
  const shownKey = lineUp.runIds.join(",");
  const runIds = useMemo(
    () => (shownKey ? shownKey.split(",") : []),
    [shownKey],
  );
  const lineUpReady = mode !== "compare" || !datasetRunsLoading;
  // On the experiment page, the experiment's runs in another group than the
  // runs shown (another dataset version, trial plan or evaluators).
  const otherIds = useMemo(
    () =>
      mode === "experiment" && experimentId
        ? otherGroupIds(
            requestedIds,
            (datasetRuns?.results ?? []).filter(
              (r) => r.experiment_id === experimentId,
            ),
          )
        : [],
    [mode, experimentId, requestedIds, datasetRuns],
  );

  const [prefs, setPrefsState] = useState(readPrefs);
  const setPrefs = (next) => {
    const merged = { ...prefs, ...next };
    setPrefsState(merged);
    writePrefs(merged);
  };
  // One view: dense rows by default on short windows (1280×720, 8c);
  // the viewer's own choice wins.
  const dense =
    ex.dense ??
    (prefs.dense != null ? Boolean(prefs.dense) : viewport === "short");
  const showCost = ex.costBars ?? prefs.showCost !== false;
  const setExtra = (next) => patchLayout({ extras: next });
  const setMoreMetrics = (v) => {
    setExtra({ moreMetrics: Boolean(v) });
    if (moreParam != null) patch({ more: v ? "1" : "0" });
  };
  const [hidden, setHidden] = useState([]);
  const [highlighted, setHighlighted] = useState(null);
  const [focus, setFocus] = useState(null);
  const [hover, setHover] = useState(null);
  const hoverTimer = useRef(null);
  const onDeltaRef = useRef(null);
  const whyRef = useRef(null);
  const leaveTimer = useRef(null);
  const gridRef = useRef(null);
  const checksSpotRef = useRef(null);
  const copyRef = useRef(null);
  // IT-R4 G6 P7: the shift-click pair and the right-click cell menu.
  const [pick, setPick] = useState(null);
  // IT-R5 G6 P3: run headers ticked for `Compare these two` (at most 2;
  // a third tick drops the oldest).
  const [pickedRuns, setPickedRuns] = useState([]);
  const togglePickRun = useCallback(
    (id) =>
      setPickedRuns((cur) =>
        cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id].slice(-2),
      ),
    [],
  );
  const [cellMenu, setCellMenu] = useState(null);

  const data = useMatrixData({
    datasetId,
    runIds,
    baselineId,
    enabled: lineUpReady,
  });
  const { matrix: rawMatrix, runsById } = data;
  // R4 implementation (MUST 4): voice calls (scenarios datasets) add call cells,
  // heat modes, persona groups, regression badges and the voice verdict.
  // Its read waits for the runs to read, as the matrix read does.
  const voiceRunIds = useMemo(
    () => (lineUpReady ? runIds : []),
    [lineUpReady, runIds],
  );
  const voice = useVoiceMatrix({
    datasetId,
    matrix: rawMatrix,
    runIds: voiceRunIds,
    baselineId,
  });
  const matrix = useMemo(
    () =>
      voice && rawMatrix
        ? {
            ...rawMatrix,
            rows: (rawMatrix.rows ?? []).map((r) => ({
              ...r,
              group_values: {
                ...r.group_values,
                persona: voice.personaLabel(voice.personaOf[r.row_id]),
              },
            })),
          }
        : rawMatrix,
    [rawMatrix, voice],
  );
  const heat = heatOf(sp.get("heat"), Boolean(voice));
  // 8a: version chips (`v3.1 (draft) · Latest`) over the dataset's runs.
  const { data: configs, held: runsHeld } = useRunConfigs(datasetId);
  const versionTags = useMemo(
    () => versionTagsOf(configs?.results ?? []),
    [configs],
  );
  // Persona groups by default on voice data (`group=none` turns them off).
  // `persona` is read raw: R2's group parser only knows R2 groups.
  const rawGroup = sp.get("group");
  const group =
    rawGroup === "persona" || rawGroup === "none"
      ? rawGroup
      : rawGroup
        ? url.group
        : voice
          ? "persona"
          : null;
  const colors = useRunColors(runIds, baselineId);
  const rowFilter = rowIdsFromPills(url.pills);
  const rowsPillActive =
    (url.pills ?? []).find((p) => p?.type === "rows") ?? null;

  // The page candidate before the model exists (same rule as candId).
  const candGuess = useMemo(
    () =>
      pageCandidateId({
        cand: sp.get("cand"),
        runIds,
        baselineId,
        runs: [
          ...(headlineRuns?.results ?? []),
          ...Object.values(runsById ?? {}),
        ].filter(
          (r, i, all) =>
            r?.run_id && all.findIndex((x) => x?.run_id === r.run_id) === i,
        ),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [search, runIds, baselineId, headlineRuns, runsById],
  );
  const model = useMemo(
    () =>
      matrix
        ? buildMatrixModel(matrix, {
            runIds,
            baselineId,
            group: group === "none" ? null : group,
            sort: url.matrixSort,
            show: url.matrixShow,
            hidden,
            rowIds: rowFilter,
            candidateId: candGuess,
          })
        : null,
    [
      matrix,
      runIds,
      baselineId,
      group,
      url.matrixSort,
      url.matrixShow,
      hidden,
      rowFilter,
      candGuess,
    ],
  );

  // IT-R4 G6 P1 (rule 27, S2): ONE candidate per URL — `cand=`, else the
  // Experiments headline's pick (G2 pickHeadline), never the run with the
  // most worse tasks. The lead verdict, the ship line and the run header
  // roles read it.
  const candParam = sp.get("cand");
  const candId = useMemo(
    () =>
      model
        ? pageCandidateId({
            cand: candParam,
            runIds: model.runIds,
            baselineId: model.baselineId,
            runs: [
              ...(headlineRuns?.results ?? []),
              ...Object.values(runsById ?? {}),
            ].filter(
              (r, i, all) =>
                r?.run_id && all.findIndex((x) => x?.run_id === r.run_id) === i,
            ),
          })
        : null,
    [model, candParam, headlineRuns, runsById],
  );

  // Rule 30: roles, not just ids — the pinned baseline is `Baseline ·
  // current`; the page candidate is `New` when it was created after the
  // baseline, else `Candidate`. `Baseline` is never put on another run.
  const roles = useMemo(() => {
    const out = {};
    const bId = model?.baselineId;
    if (!bId) return out;
    out[bId] = bId === pinnedId ? "Baseline · current" : "Baseline";
    if (candId && candId !== bId) {
      const c = runsById[candId]?.created_at;
      const b = runsById[bId]?.created_at;
      out[candId] = c && b && String(c) > String(b) ? "New" : "Candidate";
    }
    return out;
  }, [model?.baselineId, pinnedId, candId, runsById]);

  const rawRuns = new URLSearchParams(search).get("runs");
  const overCap =
    mode === "compare" &&
    rawRuns &&
    rawRuns.split(",").filter(Boolean).length > MAX_COMPARE_RUNS;

  // ---------------------------------------------------------------- pareto
  // A3-17: `Section id="frontier"` (roomy: open unless the window is short).
  // Collapsed it says its answer in one line; `chart=1|0` in a link opens or
  // folds it, and a later toggle keeps the link in step.
  // R4 implementation (8c): `Cost vs quality` is a right panel at ≥ 1440 px with 3+
  // runs (open unless hidden) and a one-line `Cost vs quality ▸` button on
  // the verdict line otherwise (the chart opens in place).
  // IT-R1 G2 P5: the chart is a dialog now (never a side panel that takes
  // width from the matrix): closed on load; `chart=1` in a link opens it.
  const [paretoOpen, setParetoOpenState] = useState(chartParam === "1");
  const [why, setWhy] = useState(null);
  const [checksAnchor, setChecksAnchor] = useState(null);
  const setParetoOpen = (open) => {
    setParetoOpenState(Boolean(open));
    if (chartParam != null && (chartParam === "1") !== Boolean(open))
      patch({ chart: open ? "1" : null });
  };
  useEffect(() => {
    if (chartParam != null) setParetoOpenState(chartParam === "1");
  }, [chartParam]);
  const pAxes = { x: prefs.paretoX ?? "cost", y: prefs.paretoY ?? "pass_at_1" };
  const pModel = useMemo(
    () =>
      matrix && model
        ? {
            ...paretoModel(matrix, {
              runIds: model.runIds,
              runsById,
              x: pAxes.x,
              y: pAxes.y,
            }),
            // L7c g6 HIDE (pareto caption, unproven by walk).
            sentence: null,
          }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [matrix, model?.runIds, runsById, pAxes.x, pAxes.y],
  );

  // ----------------------------------------------------------- drawer + pair
  const runsInView = useMemo(
    () =>
      runIds
        .map((id) => runsById[id])
        .filter(Boolean)
        .sort((a, b) =>
          a.run_id === baselineId ? -1 : b.run_id === baselineId ? 1 : 0,
        ),
    [runIds, runsById, baselineId],
  );
  const drawerView = useMemo(
    () => ({ runIds, baselineId, pills: url.pills, show: "all" }),
    [runIds, baselineId, url.pills],
  );
  const pairFor = useCallback(
    (row, runId) =>
      autoPair(row, runId, {
        baselineId,
        runIds: model?.runIds ?? runIds,
        parentOf: (id) => runsById[id]?.parent_run_id ?? null,
      }),
    [baselineId, model, runIds, runsById],
  );
  // R3 MUST 4: a cell opens S's side-by-side workspace on that task with
  // the selection's runs (the clicked run guaranteed among the lanes);
  // `from` brings Esc back here with the chips and sort intact.
  const here = `${pathname}${search}`;
  const sideBySideHref = useCallback(
    (row, { runIds: lanes, rail, baseline } = {}) =>
      taskCompareHref({
        datasetId,
        runIds: lanes ?? model?.runIds ?? runIds,
        baselineId: baseline ?? baselineId,
        rowId: row?.row_id,
        rail,
        from: here,
      }),
    [datasetId, model, runIds, baselineId, here],
  );
  const openCell = useCallback(
    (row, runId, { pairWith } = {}) => {
      setHover(null);
      const all = model?.runIds ?? runIds;
      let lanes = all.includes(runId) ? all : [...all, runId];
      let baseline = baselineId;
      if (pairWith) {
        lanes = [pairWith, runId].filter(Boolean);
        if (baselineId && lanes.includes(baselineId))
          lanes = [baselineId, ...lanes.filter((id) => id !== baselineId)];
        else baseline = pairWith;
      }
      navigate(sideBySideHref(row, { runIds: lanes, baseline }));
    },
    [model, runIds, baselineId, navigate, sideBySideHref],
  );
  // IT-R4 G6 P7: two runs side by side on a task; the pair is remembered.
  const openPair = (row, a, b) => {
    try {
      window.localStorage.setItem(LAST_PAIR_KEY, JSON.stringify([a, b]));
    } catch {
      /* private mode: not remembered */
    }
    openCell(row, b, { pairWith: a });
  };
  // The drawer closes with row/run/dtab/trial; drop the pair too.
  useEffect(() => {
    if (!url.row && url.vs) patch({ vs: null });
  }, [url.row, url.vs, patch]);

  // ----------------------------------------------------------------- hover
  const clearTimers = () => {
    clearTimeout(hoverTimer.current);
    clearTimeout(leaveTimer.current);
  };
  useEffect(() => clearTimers, []);
  // The peek never opens over the row drawer.
  const drawerOpenRef = useRef(false);
  drawerOpenRef.current = Boolean(url.row);
  const onHoverCell = useCallback((anchor, row, runId) => {
    clearTimers();
    if (drawerOpenRef.current) return;
    hoverTimer.current = setTimeout(
      () => !drawerOpenRef.current && setHover({ anchor, row, runId }),
      HOVER_DELAY,
    );
  }, []);
  useEffect(() => {
    if (url.row) setHover(null);
  }, [url.row]);
  const onLeaveCell = useCallback(() => {
    clearTimeout(hoverTimer.current);
    leaveTimer.current = setTimeout(() => setHover(null), 150);
  }, []);

  // ------------------------------------------------------------- actions
  const noteDraftHref = (row, runId) => {
    const cell = row.cells?.[runId];
    const failIdx = (cell?.trials ?? []).findIndex(
      (t) => t === 0 || t === "error",
    );
    const trial = failIdx >= 0 ? failIdx + 1 : 1;
    const label =
      runsById[runId]?.label ?? model?.columnsById[runId]?.label ?? "";
    const draft = `${label} · trial ${trial}: `;
    return `${DEVELOP}/${datasetId}?tab=data&row=${row.row_id}&rowTab=annotations&noteDraft=${encodeURIComponent(draft)}`;
  };
  const setRowsPill = (ids, label) =>
    patch({
      filters: ids
        ? JSON.stringify([
            ...(url.pills ?? []).filter((p) => p?.type !== "rows"),
            rowsPill(ids, { label }),
          ])
        : (url.pills ?? []).filter((p) => p?.type !== "rows").length
          ? JSON.stringify((url.pills ?? []).filter((p) => p?.type !== "rows"))
          : null,
    });
  const baseLabel =
    runsById[baselineId]?.label ?? model?.columnsById[baselineId]?.label ?? "";
  const deltaLabel = (runId, dir) =>
    `${runsById[runId]?.label ?? model?.columnsById[runId]?.label ?? ""} ${dir === "up" ? "better" : "worse"} than ${baseLabel}`;
  const activeDelta = (() => {
    if (!rowsPillActive?.label || !model) return null;
    for (const id of model.runIds)
      for (const dir of ["up", "down"])
        if (rowsPillActive.label === deltaLabel(id, dir))
          return { runId: id, dir };
    return null;
  })();
  const onDelta = (runId, dir, forceOn = false) => {
    const col = model?.columnsById[runId];
    if (!col) return;
    if (!forceOn && activeDelta?.runId === runId && activeDelta?.dir === dir)
      setRowsPill(null);
    else
      setRowsPill(
        dir === "up" ? col.betterRows : col.worseRows,
        deltaLabel(runId, dir),
      );
  };
  // Both pages read `baseline=`; patching keeps group, sort, show and filters.
  const setBaseline = (id) => {
    const prev = url.baseline;
    patch({ baseline: id });
    const label = runsById[id]?.label ?? model?.columnsById[id]?.label ?? "";
    notify(`${label} is the baseline for this view`, {
      key: "matrix-baseline",
      undo: { onUndo: () => patch({ baseline: prev ?? null }) },
    });
  };
  const columnMenu = (runId) => {
    const run = runsById[runId];
    return [
      {
        label: "Make baseline",
        icon: "mdi:flag-outline",
        onClick: () => setBaseline(runId),
        disabled: runId === baselineId,
      },
      {
        label: "Sort by this run, worst first",
        icon: "mdi:sort-ascending",
        onClick: () => patch({ sort: `run:${runId}` }),
      },
      // dataset integration FE-0: Test a fix only for a prompt or agent run.
      ...(!run || canIterate(run)
        ? [
            {
              label: readOnly
                ? `${VOCAB.TEST_A_FIX} on this run… (Editors can start runs)`
                : `${VOCAB.TEST_A_FIX} on this run…`,
              icon: "mdi:source-branch",
              disabled: readOnly,
              onClick: () =>
                navigate(
                  // 8e: from a lane, start on the rows it failed (the dock
                  // falls back to every row when it failed none).
                  iterateHref(`${pathname}${search}`, {
                    runId,
                    scope: "failed",
                  }),
                ),
            },
          ]
        : []),
      // dataset integration FE-0: absent for a run Future AGI cannot start again itself
      // (code, harness, voice, upload).
      ...(!run || canRerunHere(run)
        ? [
            {
              label: readOnly
                ? `${VOCAB.RERUN_WITH_CHANGES} (Editors can start runs)`
                : VOCAB.RERUN_WITH_CHANGES,
              icon: "mdi:restart",
              disabled: readOnly,
              onClick: () => openRerunWithChanges({ runId }),
            },
          ]
        : []),
      ...runActions.columnItems(runId),
      {
        label: "Same config on other datasets",
        icon: "mdi:database-search-outline",
        onClick: () =>
          navigate(
            byDatasetHref({
              datasetId,
              runIds,
              baselineId,
              cand: runId,
              configKey: run ? configKeyOf(run) : undefined,
            }),
          ),
      },
      {
        label: "Open run details",
        icon: "mdi:open-in-new",
        disabled: !run?.experiment_id,
        onClick: () =>
          navigate(`${DEVELOP}/experiment/${run.experiment_id}/data`),
      },
      {
        label: "Hide",
        icon: "mdi:eye-off-outline",
        divider: true,
        disabled: (model?.runIds.length ?? 0) <= 2,
        onClick: () => setHidden((h) => [...h, runId]),
      },
    ];
  };

  // ------------------------------------------------------- verdict (R3)
  const firstWorse = (runId) => {
    const ids = new Set(model?.columnsById[runId]?.worseRows ?? []);
    return (model?.allRows ?? []).find((r) => ids.has(r.row_id)) ?? null;
  };
  // IT-R1 G2 P1: `Task success across 30 tasks` on a task suite, `success
  // across 248 rows` elsewhere — the same words as the Experiments headline.
  const { kind: datasetKindValue } = useDatasetKind(datasetId);
  const verdictNoun = datasetKindValue === "task_suite" ? "task" : "row";
  const runActions = useMatrixRunActions({ datasetId, runIds: model?.runIds ?? [], runsById, rows: model?.allRows?.length ?? 0, noun: verdictNoun, readOnly }); // prettier-ignore
  // IT-R3 G6 P1: `See why` and `Why?` are one `Why ▾` menu (Open the worst
  // task · Show the N that got worse · Explain). The menu anchors to the
  // click position, so a re-render of the verdict never moves it.
  const [whyMenu, setWhyMenu] = useState(null);
  const whyAction = (runId, { worse, firstWorseId, unit, runLabel }) => ({
    label: "Why",
    icon: "mdi:chevron-down",
    tooltip: `Why ${runLabel} moved: open the worst ${unit}, or explain the pattern`,
    onClick: (e) => {
      const r = e?.currentTarget?.getBoundingClientRect?.();
      setWhyMenu({
        runId,
        worse,
        firstWorseId,
        unit,
        pos: r ? { top: r.bottom + 4, left: r.left } : { top: 120, left: 24 },
      });
    },
  });
  const verdictItems = useMemo(
    () =>
      // R4 implementation: on voice data the verdict is the voice verdict (task
      // completion next to latency; faster but worse never "beats").
      model && voice?.verdicts?.length
        ? // Voice vs voice first; a text-only run is not comparable on
          // latency, so its line shows only when nothing else can.
          (voice.verdicts.some((v) => v.comparable)
            ? voice.verdicts.filter((v) => v.comparable)
            : voice.verdicts
          ).map((v) => ({
            key: v.cand.runId,
            verdict: asVerdictLine(v),
            actions: [
              whyAction(v.cand.runId, {
                worse: v.worse ?? 0,
                firstWorseId: null,
                unit: "call",
                runLabel: v.cand.label,
              }),
            ],
          }))
        : model
          ? scopeVerdictInputs(verdictInputs(model, runsById), {
              matrix,
              baselineId: model.baselineId,
              noun: verdictNoun,
            }).map((v) => {
              const scoped = scopedVerdict(v);
              // IT-R3 G6 P1 (rule 21): when the pair did not run the same
              // rows, the first sentence says so (`#1D ran 24 of 30 tasks
              // (dataset v1); T25–T30 unknown.`).
              const coverage = coverageText(v.compare, {
                candidate: v.label,
                baseline: baseLabelOf(model, runsById),
                noun: verdictNoun,
                datasetKind: datasetKindValue,
                candVersion: runsById[v.runId]?.dataset_version?.number,
                baseVersion:
                  runsById[model.baselineId]?.dataset_version?.number,
              });
              const verdict = coverage
                ? {
                    ...scoped,
                    coverage,
                    parts: [
                      { t: "text", text: `${coverage}. ` },
                      ...scoped.parts,
                    ],
                    sentence: `${coverage}. ${scoped.sentence}`,
                  }
                : scoped;
              return {
                key: v.runId,
                verdict,
                actions: [
                  whyAction(v.runId, {
                    worse: v.worse ?? 0,
                    firstWorseId: v.worseRows?.[0] ?? null,
                    unit: verdictNoun,
                    runLabel: v.label,
                  }),
                ],
              };
            })
          : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [model, runsById, voice, matrix, verdictNoun, datasetKindValue],
  );

  // R4 implementation (8c): `Best value: #2A … · $0.0075 per solved task (#2D Claude
  // Code $0.21)` over every run in view (runNumbers.bestOf), next to the
  // verdicts; the same function the leaderboard uses.
  const bestValueItem = useMemo(() => {
    if (!matrix || !model || model.runIds.length < 2) return null;
    const noun = voice ? "call" : "task";
    const labels = Object.fromEntries(
      model.runIds.map((id) => [
        id,
        runsById[id]?.label ?? model.columnsById[id]?.label ?? "",
      ]),
    );
    const runs = runsOfMatrix(matrix, { labels, runIds: model.runIds });
    // IT-R2 G2 P4: the one Best value rule (runNumbers.bestValueOf): on
    // the frontier, within 10 pts of the most solved; the cheapest run is
    // named apart, never called Best value at 53%.
    const value = bestValueOf(runs, { noun });
    if (!value) return null;
    const name = (id) =>
      runShortName(runsById[id]) || model.columnsById[id]?.name || "";
    const text = bestValueText(value, { nameOf: name, noun });
    const head = `${labels[value.id]} ${name(value.id)}`.trim();
    const sentence = `${VOCAB.BEST_VALUE}: ${text}`;
    return {
      key: "best-value",
      last: true,
      verdict: {
        tone: "better",
        deltaPts: -Infinity,
        sentence,
        parts: [
          { t: "term", k: "bestValue", text: VOCAB.BEST_VALUE },
          { t: "text", text: ": " },
          { t: "run", text: head },
          { t: "text", text: text.slice(head.length) },
        ],
        chips: [],
        confidence: null,
      },
      actions: [],
    };
  }, [matrix, model, runsById, voice]);
  // R4 implementation (8d): one plain next step computed from the same scoped
  // numbers, with `Copy as text` (the verdicts + why + next step).
  const nextStepItem = useMemo(() => {
    if (voice || !matrix || !model?.baselineId) return null;
    const scoped = scopeVerdictInputs(verdictInputs(model, runsById), {
      matrix,
      baselineId: model.baselineId,
      noun: verdictNoun,
    });
    const labelOfRow = (id) =>
      model.allRows.find((r) => r.row_id === id)?.label ?? "the first one";
    // IT-R3 G6 P1 (S6): the ship / don't-ship line is part of the verdict
    // (pinned under the lead line, never behind `+N more`).
    const ship = shipLineOf(scoped, {
      candId,
      rowLabelOf: labelOfRow,
      baseLabel: baseLabelOf(model, runsById),
      candName: candId
        ? whoText(
            runsById[candId],
            runsById[candId]?.label ?? model.columnsById[candId]?.label,
          )
        : undefined,
      baseName: whoText(
        runsById[model.baselineId],
        runsById[model.baselineId]?.label ??
          model.columnsById[model.baselineId]?.label,
      ),
      noun: verdictNoun,
      datasetKind: datasetKindValue,
    });
    if (!ship) return null;
    const text = ship.text;
    const copy = async () => {
      const paragraph = verdictsAsText(
        [...verdictItems.map((i) => i.verdict), bestValueItem?.verdict].filter(
          Boolean,
        ),
        { nextStep: text },
      );
      const ok = await copyText(paragraph);
      notify(ok ? "Verdicts copied as text" : "Couldn't copy", {
        variant: ok ? "success" : "error",
      });
    };
    const labelOfRunId = (id) =>
      runsById[id]?.label ?? model.columnsById[id]?.label ?? "";
    const open =
      ship.rowId && ship.runId
        ? {
            label: `Open ${ship.rowLabel}`,
            tooltip: `${labelOfRunId(ship.runId)} next to ${baseLabelOf(model, runsById)} on ${ship.rowLabel}, side by side`,
            href: taskCompareHref({
              datasetId,
              runIds: [model.baselineId, ship.runId],
              baselineId: model.baselineId,
              rowId: ship.rowId,
              from: here,
            }),
          }
        : null;
    copyRef.current = copy;
    return {
      key: "next-step",
      last: true,
      pin: true,
      // IT-R4 G6 P3: the row is the sentence's own link (`… fails [T25] …`).
      link:
        open && ship.rowLabel
          ? { text: ship.rowLabel, href: open.href, tooltip: open.tooltip }
          : null,
      verdict: {
        tone: ship.tone,
        deltaPts: -Infinity,
        sentence: text,
        parts: [{ t: "text", text }],
        chips: [],
        confidence: null,
      },
      actions: [open, { label: "Copy as text", onClick: copy }].filter(Boolean),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    voice,
    matrix,
    model,
    runsById,
    verdictItems,
    bestValueItem,
    verdictNoun,
    datasetKindValue,
    datasetId,
    candId,
  ]);
  const foldItems = useMemo(
    () => [...verdictItems, bestValueItem, nextStepItem].filter(Boolean),
    [verdictItems, bestValueItem, nextStepItem],
  );

  onDeltaRef.current = onDelta;
  // R4 implementation (8d): `Why?` opens an anchored panel (the pattern Falcon
  // found, the top causes, and the first failing task side by side).
  whyRef.current = (runId, anchor) => {
    if (anchor) {
      setWhy({ runId, anchor });
      return;
    }
    const row = firstWorse(runId);
    if (row) openCell(row, runId);
  };

  // ---------------------------------------------------- keys + palette (R3)
  const focusedRow = () => {
    const list = model?.groups
      ? model.groups.flatMap((g) => g.rows)
      : model?.rows ?? [];
    return list[focus?.r ?? 0] ?? list[0] ?? null;
  };
  const toggleMore = () => setMoreMetrics(!moreMetrics);
  useShortcuts(
    "matrix",
    {
      moreMetrics: toggleMore,
      // ⇧R: Rerun with changes… for the focused run's column (else the
      // first run that is not the baseline).
      rerunWithChanges: () => {
        const id =
          (focus && focus.c >= 0 ? model?.runIds?.[focus.c] : null) ??
          model?.runIds?.find((r) => r !== baselineId) ??
          null;
        if (id && !readOnly && (!runsById[id] || canRerunHere(runsById[id])))
          openRerunWithChanges({ runId: id });
      },
      makeBaseline: () => {
        const id =
          focus && focus.c >= 0 ? model?.runIds?.[focus.c] ?? null : null;
        if (id && id !== baselineId) setBaseline(id);
        else
          notify("Focus a run's cell first (arrow keys), then press B", {
            key: "matrix-baseline-hint",
            variant: "info",
          });
      },
    },
    { enabled: Boolean(model) && !url.row },
  );
  const paletteRow = model ? focusedRow() : null;
  const baseLabel0 = baseLabelOf(model, runsById);
  useRegisterCommands(
    "matrix-view",
    model
      ? [
          paletteRow && {
            id: "matrix.sideBySide",
            label: `Open side by side for ${paletteRow.label}`,
            keywords: ["compare", "lanes", "task", "trajectory"],
            shortcut: "open",
            icon: "mdi:compare-horizontal",
            run: () =>
              navigate(sideBySideHref(paletteRow, { runIds: model.runIds })),
          },
          ...model.runIds
            .filter((id) => id !== baselineId)
            .flatMap((id) => {
              const label =
                runsById[id]?.label ?? model.columnsById[id]?.label ?? "";
              return [
                {
                  id: `matrix.baseline.${id}`,
                  label: `Make ${label} baseline`,
                  keywords: ["baseline", "compare against", label],
                  icon: "mdi:flag-outline",
                  run: () => setBaseline(id),
                },
                {
                  id: `matrix.pair.${id}`,
                  label: `Compare ${label} vs ${baseLabel0}`,
                  keywords: ["side by side", "pair", label],
                  icon: "mdi:compare-horizontal",
                  run: () =>
                    navigate(
                      sideBySideHref(firstWorse(id), {
                        runIds: [baselineId, id].filter(Boolean),
                      }),
                    ),
                },
              ];
            }),
          {
            id: "matrix.regressions",
            label: "Show regressions only",
            keywords: ["worse", "regressed", "got worse"],
            icon: "mdi:trending-down",
            run: () => {
              const ids = [
                ...new Set(model.columns.flatMap((c) => c.worseRows ?? [])),
              ];
              setRowsPill(ids, `Got worse than ${baseLabel0}`);
            },
          },
          {
            id: "matrix.moreMetrics",
            label: moreMetrics ? "Fewer metrics" : "More metrics",
            keywords: ["pass^3", "reliable", "time per task", "p50"],
            shortcut: "moreMetrics",
            icon: "mdi:table-row-plus-after",
            run: toggleMore,
          },
          {
            id: "matrix.chart",
            label: paretoOpen ? "Hide chart" : "Show chart",
            keywords: ["frontier", "pareto", "cost vs success"],
            icon: "mdi:chart-scatter-plot",
            run: () => setParetoOpen(!paretoOpen),
          },
        ].filter(Boolean)
      : [],
    [
      model,
      runsById,
      baselineId,
      paletteRow?.row_id,
      moreMetrics,
      paretoOpen,
      here,
    ],
  );

  // -------------------------------------------------------------- keyboard
  const flatRows = model?.rows ?? [];
  const visibleRows = model?.groups
    ? model.groups.flatMap((g) => g.rows)
    : flatRows;
  const onKeyDown = (e) => {
    if (!model) return;
    const maxR = visibleRows.length - 1;
    const maxC = model.runIds.length - 1;
    const cur = focus ?? { r: 0, c: 0 };
    let next = null;
    if (e.key === "ArrowDown")
      next = { r: Math.min(maxR, cur.r + 1), c: cur.c };
    else if (e.key === "ArrowUp")
      next = { r: Math.max(0, cur.r - 1), c: cur.c };
    else if (e.key === "ArrowRight")
      next = { r: cur.r, c: Math.min(maxC, cur.c + 1) };
    else if (e.key === "ArrowLeft")
      next = { r: cur.r, c: Math.max(-1, cur.c - 1) };
    else if (e.key === "Enter" && focus) {
      e.preventDefault();
      const row = visibleRows[focus.r];
      if (!row) return;
      if (focus.c < 0)
        patch(
          { row: row.row_id, run: null, dtab: null, vs: null },
          { push: true },
        );
      else openCell(row, model.runIds[focus.c]);
      return;
    } else if (e.key === "Escape" && hover) {
      e.preventDefault();
      e.stopPropagation();
      setHover(null);
      return;
    }
    if (!next) return;
    e.preventDefault();
    setHover(null);
    setFocus(next);
    if (next.c >= 0)
      document
        .getElementById(cellDomId(next.r, next.c))
        ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  useEffect(() => {
    if (!hover) return undefined;
    const onEsc = (e) => e.key === "Escape" && setHover(null);
    window.addEventListener("keydown", onEsc, true);
    return () => window.removeEventListener("keydown", onEsc, true);
  }, [hover]);

  // The runs left out of the read (their own group), named in one line.
  const recordOf = (id) =>
    (datasetRuns?.results ?? []).find((r) => r.run_id === id);
  const runName = (id) =>
    runsById[id]?.label ?? recordOf(id)?.label ?? recordOf(id)?.name ?? null;
  const leftLine = leftOutLine(requestedIds, lineUp.left, runName);
  // One run shown on the experiment page while its other runs can't be
  // lined up with it: the same line, in place of "Pick at least 2 runs".
  const emptyLine =
    leftLine ??
    (runIds.length === 1 && otherIds.length
      ? leftOutLine([...runIds, ...otherIds], otherIds, runName)
      : null);

  // ---------------------------------------------------------------- states
  if (!datasetId || ((data.isLoading || !lineUpReady) && !matrix))
    return (
      <Stack sx={{ flex: 1, py: 1 }}>
        <LoadingGrid />
      </Stack>
    );
  if (data.isError && !matrix)
    return (
      <Box sx={{ py: 1 }}>
        <Notice
          tone="error"
          icon="mdi:alert-circle-outline"
          action={
            <Button
              size="small"
              variant="outlined"
              onClick={() => data.refetch()}
            >
              Retry
            </Button>
          }
        >
          {isMixedRunsRefusal(data.error)
            ? MIXED_RUNS_WORDS
            : "Couldn’t load the matrix."}
        </Notice>
      </Box>
    );
  if (runIds.length < 2 || !model)
    return (
      <Stack alignItems="center" spacing={1.25} sx={{ py: 8 }}>
        <Iconify
          icon="mdi:table-large"
          width={28}
          sx={{ color: "text.disabled" }}
        />
        {emptyLine ? (
          <Typography
            typography="s1"
            color="text.secondary"
            align="center"
            data-testid="matrix-left-out"
          >
            {emptyLine}
          </Typography>
        ) : (
          <Typography typography="s1" color="text.secondary" align="center">
            Pick at least 2 runs to compare. Matrix works with any runs on this
            dataset.
          </Typography>
        )}
        <RunPickerPopover
          datasetId={datasetId}
          excludeIds={requestedIds}
          label="Add runs"
          onPick={(ids) =>
            navigate(
              matrixHref({
                mode: "compare",
                datasetId,
                runIds: [...requestedIds, ...ids],
                baselineId: baselineId ?? requestedIds[0],
              }),
            )
          }
        />
      </Stack>
    );

  const versionOf = (id) => runsById[id]?.dataset_version?.number ?? null;
  const banner = versionsBanner(model.runIds, versionOf);
  const notInVersion = (id) =>
    banner && versionOf(id) != null ? `v${versionOf(id)}` : null;
  const pattern = data.rootCauses?.pattern;
  const patternRun = pattern
    ? runsById[pattern.run_id] ?? {
        label: matrix.columns?.[pattern.run_id]?.label,
      }
    : null;
  const patternCause = pattern
    ? data.rootCauses.causes.find(
        (c) =>
          c.run_id === pattern.run_id &&
          c.first_divergence &&
          c.rows?.some((r) => pattern.rows.includes(r.row_id)),
      ) ?? null
    : null;
  const patternLabel = `Only ${patternRun?.label ?? ""} fails`;
  const patternActive =
    Boolean(pattern) && rowsPillActive?.label === patternLabel;
  // W28: a try is done once it ran, with or without a pass / fail (a score
  // with no pass rule is done, not still to run); stopped or never-reached
  // tries are not counted.
  const tries = matrixTries(matrix, model.runIds);
  // L7c g5 (ledger:r1): the header counts tries from the runs' one ledger
  // (R1 `ledger.tries`): a try is done when it scored, was skipped or N/A,
  // errored or was left out; tries a Stop left unplayed are not counted. A
  // skipped try never reads as still to run, so a finished experiment
  // never says Running.
  const triesLedger = mergeLedgers(
    model.runIds.map((id) => runsById?.[id]?.ledger),
  )?.tries;
  const ledgerTotal = triesLedger
    ? (triesLedger.total ?? 0) - (triesLedger.stopped ?? 0)
    : null;
  const ledgerDone = triesLedger
    ? (triesLedger.done ?? 0) + (triesLedger.excluded ?? 0)
    : null;
  const shownDone = ledgerTotal ? ledgerDone : tries.done;
  const shownTotal = ledgerTotal ? ledgerTotal : tries.total;
  // `Running · 3 of 8 tries`, or `Completed · 8 of 8 tries` once every run
  // in view completed (the runs' own status, R3 `columns[id].status`).
  const status = matrixStatusText({
    running: data.running,
    done: shownDone,
    total: shownTotal,
    statuses: model.runIds.map(
      (id) => matrix.columns?.[id]?.status ?? runsById[id]?.status,
    ),
  });
  const runningText = status?.running ? status.text : null;
  const finishedText = status && !status.running ? status.text : null;

  const metaKeys = Object.keys(matrix.rows?.[0]?.group_values ?? {}).filter(
    (k) =>
      !["category", "difficulty", "split", "source", "title"].includes(k) &&
      new Set((matrix.rows ?? []).map((r) => r.group_values?.[k] ?? null))
        .size <= Math.max(2, (matrix.rows?.length ?? 0) / 2),
  );
  const groupOptions = [
    ...(voice ? [{ value: "persona", label: "Persona" }] : []),
    ...GROUP_OPTIONS.filter((o) => o.value !== "none"),
    ...metaKeys.map((k) => ({ value: `meta:${k}`, label: k })),
    { value: "none", label: "None" },
  ];

  const hoverCause =
    hover &&
    data.rootCauses?.causes?.find(
      (c) =>
        c.run_id === hover.runId &&
        c.first_divergence &&
        c.rows?.some((r) => r.row_id === hover.row.row_id),
    );
  const hoverPair = hover ? pairFor(hover.row, hover.runId) : null;

  // A3-18: the bottom lines are always there. Short windows fold them to
  // their one-line answer (`Row checks` Section, roomy); they teach where
  // the eval, not the agent, may be wrong.
  const anyLine = model.groups
    ? model.groups.map((g) => `${g.key} ${g.anySolved}/${g.count}`).join(" · ")
    : `${model.anySolved}/${model.rows.length}`;
  const rowsWord = (n) => `${n} ${n === 1 ? "row" : "rows"}`;
  const bottomSummary = [
    `Any run passes every trial ${anyLine}`,
    model.nobody.length
      ? `${rowsWord(model.nobody.length)} no run passes every time`
      : null,
    model.everyone.length
      ? `${rowsWord(model.everyone.length)} every run passes every time`
      : null,
  ]
    .filter(Boolean)
    .join(" · ");
  // 8a: `+ Add run` (every run of the dataset not in view, the pinned
  // baseline included) and the lane `×` below.
  // IT-R4 G6 P2: Add run keeps your page — runs you removed stay removed,
  // the candidate stays (`cand=`), the new runs append at the end.
  const addRuns = (ids) => {
    const kept = requestedIds.filter((id) => !hidden.includes(id));
    const next = [...kept, ...ids.filter((id) => !kept.includes(id))];
    if (mode === "compare") {
      patch({ runs: next.join(","), cand: candParam ?? candId ?? null });
      return;
    }
    const href = matrixHref({
      mode: "compare",
      datasetId,
      runIds: next,
      baselineId: baselineId ?? runIds[0],
    });
    const [path, query = ""] = href.split("?");
    const q = new URLSearchParams(query);
    if (candId && !q.get("cand")) q.set("cand", candId);
    ["group", "sort", "show", "heat"].forEach((k) => {
      if (sp.get(k) && !q.get(k)) q.set(k, sp.get(k));
    });
    if (!q.get("from")) q.set("from", `${pathname}${search}`);
    navigate(`${path}?${q.toString()}`);
  };
  const removeRun = (id) => {
    if (id === baselineId || model.runIds.length <= 2) return;
    if (mode === "compare") {
      const next = requestedIds.filter((r) => r !== id);
      patch({
        runs: next.join(","),
        cand: sp.get("cand") === id ? null : sp.get("cand"),
      });
      notify(`${runsById[id]?.label ?? "Run"} removed from this comparison`, {
        key: "matrix-remove-run",
        undo: { onUndo: () => patch({ runs: requestedIds.join(",") }) },
      });
    } else setHidden((h) => [...h, id]);
  };
  // R4 implementation (8c): Row checks open from the header cell (`Row checks ▸`,
  // its one-line answer in the tooltip), so the rows keep the height.
  // IT-R1 G2 P6: the corner keeps `Add run`; `Row checks` sits in the
  // toolbar (next to Sort) and `More metrics` in View, so the corner never
  // overlaps at 1280.
  // IT-R5 G6 P3: two ticked headers → task compare on that pair (S2: runs,
  // baseline, cand). The page baseline stays the baseline when ticked.
  const picked2 = pickedRuns.filter((id) => model?.runIds?.includes(id));
  const pickedPair =
    picked2.length === 2
      ? picked2.includes(baselineId)
        ? [baselineId, picked2.find((id) => id !== baselineId)]
        : picked2
      : null;
  const labelOfRun = (id) =>
    runsById[id]?.label ?? model?.columnsById?.[id]?.label ?? "run";
  const addRunButton = pickedPair ? (
    <Stack gap={0.5} data-testid="matrix-compare-picked">
      <Button
        size="small"
        variant="outlined"
        startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
        onClick={() =>
          navigate(
            `${taskCompareHref({
              datasetId,
              runIds: pickedPair,
              baselineId: pickedPair[0],
              from: here,
            })}&cand=${encodeURIComponent(pickedPair[1])}`,
          )
        }
        sx={{ justifyContent: "flex-start", whiteSpace: "nowrap" }}
      >
        Compare these two
      </Button>
      <Typography typography="s3" color="text.secondary" noWrap>
        {labelOfRun(pickedPair[1])} vs {labelOfRun(pickedPair[0])} ·{" "}
        <Link
          component="button"
          type="button"
          underline="hover"
          onClick={() => setPickedRuns([])}
          sx={{ typography: "s3", verticalAlign: "baseline" }}
        >
          Clear
        </Link>
      </Typography>
    </Stack>
  ) : (
    <RunPickerPopover
      datasetId={datasetId}
      excludeIds={requestedIds.filter((id) => !hidden.includes(id))}
      label="Add run"
      onPick={addRuns}
    />
  );
  // IT-R4 G6 P3: `Row checks` opens from the View menu; the popover
  // anchors to this spot in the toolbar.
  const rowChecks = (
    <>
      <Box
        ref={checksSpotRef}
        aria-hidden
        sx={{ width: 0, height: 24, alignSelf: "center" }}
      />
      <Popover
        open={Boolean(checksAnchor)}
        anchorEl={checksAnchor}
        onClose={() => setChecksAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 640, maxWidth: "92vw", py: 1 } } }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ px: 1.5, pb: 0.5 }}
        >
          Row checks
        </Typography>
        <Box sx={{ pb: 0.5 }}>
          <BottomLine label="Any run passes every trial">
            {model.groups
              ? model.groups
                  .map((g) => `${g.key} ${g.anySolved}/${g.count}`)
                  .join(" · ")
              : `${model.anySolved}/${model.rows.length}`}
          </BottomLine>
          {model.nobody.length > 0 && (
            <BottomLine
              label={`${model.nobody.length} ${model.nobody.length === 1 ? "row" : "rows"} no run passes every time`}
              action={
                <CalloutAction
                  tooltip={`No run passes ${model.nobody[0].label} in all trials. Check the row and its evals before blaming the agents.`}
                  onClick={() =>
                    navigate(
                      `${datasetGridHref(
                        datasetId,
                        [
                          rowIdsFilter(
                            model.nobody.map((r) => r.id),
                            { label: "No run passes every time" },
                          ),
                        ],
                        { row: model.nobody[0].id },
                      )}&rowTab=row`,
                    )
                  }
                >
                  Review these rows →
                </CalloutAction>
              }
            >
              {labelList(model.nobody)}
            </BottomLine>
          )}
          {model.everyone.length > 0 && (
            <BottomLine
              label={`${model.everyone.length} ${model.everyone.length === 1 ? "row" : "rows"} every run passes every time`}
              action={
                <CalloutAction
                  onClick={() =>
                    navigate(
                      datasetGridHref(datasetId, [
                        rowIdsFilter(
                          model.everyone.map((r) => r.id),
                          { label: "Every run passes" },
                        ),
                      ]),
                    )
                  }
                >
                  Consider harder variants →
                </CalloutAction>
              }
            >
              {labelList(model.everyone)}
            </BottomLine>
          )}
        </Box>
      </Popover>
    </>
  );

  const extra = (
    <>
      {rowChecks}
      {/* IT-R1 G2 P6: the version-mismatch banner is one inline chip. */}
      {banner ? (
        <CustomTooltip show size="small" placement="bottom" title={banner.text}>
          <Chip
            size="small"
            variant="outlined"
            data-testid="matrix-versions-chip"
            icon={<Iconify icon="mdi:source-branch" width={14} />}
            label={
              <span>
                Different dataset versions{" "}
                <Iconify
                  icon="mdi:information-outline"
                  width={13}
                  sx={{ verticalAlign: "-2px" }}
                />
              </span>
            }
            sx={{ height: 26, typography: "s2" }}
          />
        </CustomTooltip>
      ) : null}
      {rowsPillActive && (
        <Chip
          size="small"
          label={`${rowsPillActive.label ?? "Rows"} (${rowsPillActive.ids.length})`}
          onDelete={() => setRowsPill(null)}
          sx={{ height: 26, typography: "s2" }}
        />
      )}
      {hidden.length > 0 && (
        <Chip
          size="small"
          variant="outlined"
          label={`${hidden.length} hidden · Show all`}
          onClick={() => setHidden([])}
          sx={{ height: 26, typography: "s2" }}
        />
      )}
    </>
  );

  const grid = (
    <Stack sx={{ flex: 1, minWidth: 0, minHeight: 0, height: "100%" }}>
      {model.rows.length === 0 ? (
        <Stack alignItems="center" spacing={1} sx={{ py: 6 }}>
          <Typography typography="s1" color="text.secondary">
            {`No rows match "${model.show && model.show !== "all" ? { disagree: "Disagree", flaky: "Flaky", nobody: "Nobody passes every time", everyone: "Everyone passes" }[model.show] : "these filters"}".`}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              patch({ show: null });
              setRowsPill(null);
            }}
          >
            {`Show all ${model.showCounts.all}`}
          </Button>
        </Stack>
      ) : (
        <MatrixGrid
          ref={gridRef}
          model={model}
          runsById={runsById}
          colors={colors}
          dense={dense}
          showCost={showCost}
          focus={focus}
          onFocusGrid={() => !focus && setFocus({ r: 0, c: 0 })}
          onKeyDown={onKeyDown}
          onOpenCell={(row, runId, at, e) => {
            setFocus(at);
            // IT-R4 G6 P7: shift-click two cells → `Compare these 2`.
            if (e?.shiftKey) {
              e.preventDefault?.();
              if (!pick) {
                setPick({ row, runId });
                return;
              }
              if (pick.runId === runId && pick.row.row_id === row.row_id) {
                setPick(null);
                return;
              }
              setPick({ ...pick, second: { row, runId } });
              return;
            }
            openCell(row, runId);
          }}
          onCellMenu={(e, row, runId) =>
            setCellMenu({
              row,
              runId,
              pos: { top: e.clientY + 2, left: e.clientX + 2 },
            })
          }
          onHoverCell={onHoverCell}
          onLeaveCell={onLeaveCell}
          onOpenRow={(row) =>
            patch(
              { row: row.row_id, run: null, dtab: null, vs: null },
              { push: true },
            )
          }
          columnMenu={columnMenu}
          activeDelta={activeDelta}
          onDelta={onDelta}
          highlighted={highlighted}
          notInVersion={notInVersion}
          headerExtra={addRunButton}
          legend={
            <LegendHelp
              voice={Boolean(voice)}
              carriedFrom={
                (matrix?.rows ?? [])
                  .flatMap((r) => Object.values(r.cells ?? {}))
                  .find((c) => c?.carried_from)?.carried_from ?? undefined
              }
            />
          }
          moreMetrics={moreMetrics}
          onMoreMetrics={setMoreMetrics}
          onCompareRow={(row) =>
            navigate(sideBySideHref(row, { runIds: model.runIds }))
          }
          heat={heat}
          voice={voice}
          versionTags={versionTags}
          roles={runActions.withLines(roles)}
          onRemoveRun={removeRun}
          pickedRuns={pickedRuns}
          onPickRun={togglePickRun}
          onGroupRows={(g) =>
            setRowsPill(
              g.rows.map((r) => r.row_id),
              g.key,
            )
          }
        />
      )}
    </Stack>
  );

  const canChart = !url.row && paretoPlottable(pModel);
  // IT-R1 G2 P5: Cost vs quality opens in a large dialog (380 px chart, run
  // names on the points, ≤ 5 cost ticks, Best value badge): it never takes
  // width from the matrix. `Show chart` / `chart=1` open the same dialog.
  const sidePanel = false;
  const chartDialogOpen = Boolean(canChart && paretoOpen);
  const pareto = (onHide, big = false) => (
    <ParetoStrip
      inSection
      height={big ? 380 : undefined}
      names={big}
      model={pModel}
      extraSentence={data.frontierShift}
      colors={colors}
      runsById={runsById}
      open
      onToggle={onHide}
      x={pAxes.x}
      y={pAxes.y}
      onAxis={(a) =>
        setPrefs({
          ...(a.x ? { paretoX: a.x } : {}),
          ...(a.y ? { paretoY: a.y } : {}),
        })
      }
      highlighted={highlighted}
      onPick={(id, isolate) => {
        if (isolate) {
          setHidden(model.runIds.filter((r) => r !== id && r !== baselineId));
          setHighlighted(id);
        } else setHighlighted((h) => (h === id ? null : id));
      }}
    />
  );
  const costButton =
    canChart && !sidePanel ? (
      <CustomTooltip
        show
        size="small"
        placement="bottom"
        title={`Cost vs quality · ${frontierLine(pModel)}`}
      >
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          data-testid="cost-vs-quality"
          onClick={() => setParetoOpen(true)}
          startIcon={<Iconify icon="mdi:chart-scatter-plot" width={16} />}
          sx={{
            typography: "s2",
            whiteSpace: "nowrap",
            flexShrink: 0,
            height: 28,
            borderColor: "divider",
          }}
        >
          Cost vs quality
        </Button>
      </CustomTooltip>
    ) : null;

  // IT-R4 G6 P6: the Explain popover says what it covers.
  const whyCoverage = (() => {
    if (!why || !matrix || !model?.baselineId || !pattern?.rows?.length)
      return null;
    const item = scopeVerdictInputs(verdictInputs(model, runsById), {
      matrix,
      baselineId: model.baselineId,
      noun: verdictNoun,
    }).find((v) => v.runId === why.runId);
    if (!item?.compare) return null;
    const rowsById = new Map((matrix.rows ?? []).map((r) => [r.row_id, r]));
    return explainCoverage({
      compare: item.compare,
      patternRows: pattern.rows,
      cause: patternCause?.title ?? pattern.common_feature ?? null,
      noun: verdictNoun,
      baseRateOf: (id) => {
        const t = rowsById.get(id)?.cells?.[model.baselineId]?.trials ?? [];
        const scored = t.filter((x) => x !== "infra" && x != null);
        return scored.length
          ? scored.filter((x) => x === 1 || x === true || x === "pass").length /
              scored.length
          : null;
      },
    });
  })();

  // IT-R4 G6 P6: `Why` is a visible outlined split button — the main part
  // opens the worst task (call) side by side, the caret opens the menu
  // (Show the N that got worse · Explain).
  const leadItem =
    verdictItems.find((i) => i.key === candId) ?? verdictItems[0] ?? null;
  const leadWhy = leadItem?.actions?.find((a) => a.label === "Why") ?? null;
  const whyUnit = voice ? "call" : verdictNoun;
  const whyButton = leadWhy ? (
    <CustomTooltip
      show
      size="small"
      placement="bottom"
      title={`Open the worst ${whyUnit} side by side, show the ones that got worse, or explain the pattern`}
    >
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        aria-haspopup="menu"
        aria-label={`Why: open the worst ${whyUnit}, show the ones that got worse, or explain`}
        data-testid="matrix-why"
        onClick={(e) => leadWhy.onClick(e)}
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        sx={{
          typography: "s2",
          whiteSpace: "nowrap",
          flexShrink: 0,
          height: 28,
          borderColor: "divider",
        }}
      >
        {/* S6: one label everywhere, `Why ▾` (the caret is the icon). */}
        Why
      </Button>
    </CustomTooltip>
  ) : null;

  return (
    <Stack sx={{ flex: 1, minHeight: 0, gap: 1 }} data-testid="matrix-view">
      {/* A3-08: one line + `+N more`, the same fold as run Data. */}
      <Stack
        direction="row"
        alignItems="center"
        sx={{ px: 1, minWidth: 0, gap: 1 }}
      >
        {runsHeld ? (
          <Typography
            role="status"
            typography="s3"
            color="text.secondary"
            noWrap
            data-testid="matrix-runs-not-in-release"
          >
            {`Run history: ${NOT_IN_RELEASE}`}
          </Typography>
        ) : null}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <VerdictFold
            items={foldItems}
            surface="matrix"
            testId="matrix-verdict"
            leadKey={candId ?? undefined}
            oneLine
            after={
              <>
                {whyButton}
                {costButton}
              </>
            }
            moreActions={
              nextStepItem
                ? [
                    {
                      label: "Copy as text",
                      icon: "mdi:content-copy",
                      onClick: () => copyRef.current?.(),
                    },
                  ]
                : []
            }
          />
        </Box>
      </Stack>
      <Dialog
        open={chartDialogOpen}
        onClose={() => setParetoOpen(false)}
        maxWidth="lg"
        fullWidth
        aria-labelledby="matrix-cost-quality-title"
        PaperProps={{ sx: { p: 2 }, "data-testid": "cost-vs-quality-panel" }}
      >
        <Typography
          id="matrix-cost-quality-title"
          typography="m3"
          fontWeight="fontWeightSemiBold"
          sx={{ mb: 1 }}
        >
          Cost vs quality
        </Typography>
        {chartDialogOpen && pModel
          ? pareto(() => setParetoOpen(false), true)
          : null}
      </Dialog>
      <MatrixToolbar
        moreMetrics={moreMetrics}
        onMoreMetrics={setMoreMetrics}
        onResetView={() => {
          if (moreParam != null || chartParam != null)
            patch({ more: null, chart: null });
        }}
        onReview={(rail) =>
          navigate(
            taskCompareHref({
              datasetId,
              runIds: model.runIds,
              baselineId,
              rail,
              from: here,
            }),
          )
        }
        headerText={model.headerText}
        running={runningText}
        finished={finishedText}
        group={model.group}
        groupOptions={groupOptions}
        onGroup={(g) =>
          patch({
            group:
              g === "none"
                ? voice
                  ? "none"
                  : null
                : g === "persona" && voice
                  ? null
                  : g,
          })
        }
        heat={heat}
        heatOptions={heatOptionsFor(Boolean(voice))}
        onHeat={(h) => patch({ heat: h === "result" ? null : h })}
        heatLegend={heat === "latency" ? heatLegend().join(" · ") : null}
        sort={model.sort}
        onSort={(s) => patch({ sort: s === "disagreement" ? null : s })}
        show={model.show}
        showCounts={model.showCounts}
        onShow={(s) => patch({ show: s === "all" ? null : s })}
        dense={dense}
        onDense={(v) => setExtra({ dense: Boolean(v) })}
        showCost={showCost}
        onShowCost={(v) => setExtra({ costBars: Boolean(v) })}
        k={model.trials}
        extra={extra}
        defaultGroup={voice ? "persona" : "none"}
        viewActions={[
          {
            id: "row-checks",
            label: "Row checks…",
            icon: "mdi:table-check",
            onClick: () => setChecksAnchor(checksSpotRef.current),
          },
        ]}
      />
      {pick ? (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          data-testid="matrix-pair-bar"
          sx={{
            mx: 1,
            px: 1,
            py: 0.5,
            borderRadius: 0.5,
            border: "1px solid",
            borderColor: "primary.main",
            bgcolor: (t) => alpha(t.palette.primary.main, 0.06),
          }}
        >
          <Typography typography="s2" sx={{ flex: 1, minWidth: 0 }} noWrap>
            {pick.second
              ? `${runsById[pick.runId]?.label ?? ""} on ${pick.row.label} and ${runsById[pick.second.runId]?.label ?? ""} on ${pick.second.row.label}`
              : `${runsById[pick.runId]?.label ?? ""} on ${pick.row.label} picked. Shift-click a second cell to compare the two.`}
          </Typography>
          {pick.second ? (
            <Button
              size="small"
              variant="contained"
              data-testid="matrix-compare-pair"
              onClick={() => {
                const a = pick;
                setPick(null);
                openPair(a.row, a.runId, a.second.runId);
              }}
              sx={{ typography: "s2", height: 26 }}
            >
              Compare these 2
            </Button>
          ) : null}
          <Button
            size="small"
            color="inherit"
            onClick={() => setPick(null)}
            sx={{ typography: "s2", height: 26 }}
          >
            Cancel
          </Button>
        </Stack>
      ) : null}
      <Menu
        open={Boolean(cellMenu)}
        onClose={() => setCellMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={cellMenu?.pos}
        data-testid="matrix-cell-menu"
      >
        {cellMenu
          ? [
              cellMenu.runId !== model?.baselineId && model?.baselineId ? (
                <MenuItem
                  key="vs-base"
                  onClick={() => {
                    const m = cellMenu;
                    setCellMenu(null);
                    openPair(m.row, model.baselineId, m.runId);
                  }}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:compare-horizontal" width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary="Side by side with baseline"
                    secondary={`${runsById[cellMenu.runId]?.label ?? ""} next to ${baseLabelOf(model, runsById)} on ${cellMenu.row.label}`}
                    primaryTypographyProps={{ typography: "s1" }}
                    secondaryTypographyProps={{ typography: "s3" }}
                  />
                </MenuItem>
              ) : null,
              (() => {
                const last = readLastPair();
                const other = last?.find(
                  (id) => id !== cellMenu.runId && model?.runIds?.includes(id),
                );
                if (!other || other === model?.baselineId) return null;
                return (
                  <MenuItem
                    key="last-pair"
                    onClick={() => {
                      const m = cellMenu;
                      setCellMenu(null);
                      openPair(m.row, other, m.runId);
                    }}
                  >
                    <ListItemIcon>
                      <Iconify icon="mdi:history" width={18} />
                    </ListItemIcon>
                    <ListItemText
                      primary={`Side by side with ${runsById[other]?.label ?? ""}`}
                      secondary="Your last pair"
                      primaryTypographyProps={{ typography: "s1" }}
                      secondaryTypographyProps={{ typography: "s3" }}
                    />
                  </MenuItem>
                );
              })(),
              ...runActions.cellItems(cellMenu, () => setCellMenu(null)),
              <MenuItem
                key="pick"
                onClick={() => {
                  const m = cellMenu;
                  setCellMenu(null);
                  setPick({ row: m.row, runId: m.runId });
                }}
              >
                <ListItemIcon>
                  <Iconify
                    icon="mdi:selection-ellipse-arrow-inside"
                    width={18}
                  />
                </ListItemIcon>
                <ListItemText
                  primary="Pick for a pair…"
                  secondary="Then shift-click a second cell"
                  primaryTypographyProps={{ typography: "s1" }}
                  secondaryTypographyProps={{ typography: "s3" }}
                />
              </MenuItem>,
            ].filter(Boolean)
          : null}
      </Menu>

      {overCap && (
        <Notice>{`Compare up to ${MAX_COMPARE_RUNS} runs at a time. Showing the first ${MAX_COMPARE_RUNS}.`}</Notice>
      )}
      {leftLine ? <Notice>{leftLine}</Notice> : null}

      <Menu
        open={Boolean(whyMenu)}
        onClose={() => setWhyMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={whyMenu?.pos}
        slotProps={{ paper: { sx: { minWidth: 280 } } }}
        data-testid="matrix-why-menu"
      >
        {whyMenu
          ? [
              whyMenu.firstWorseId || whyMenu.worse > 0 ? (
                <MenuItem
                  key="worst"
                  onClick={() => {
                    const m = whyMenu;
                    setWhyMenu(null);
                    if (m.firstWorseId)
                      navigate(
                        taskCompareHref({
                          datasetId,
                          runIds: [model.baselineId, m.runId],
                          baselineId: model.baselineId,
                          rowId: m.firstWorseId,
                          from: here,
                        }),
                      );
                    else whyRef.current?.(m.runId);
                  }}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:compare-horizontal" width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary={`Open the worst ${whyMenu.unit}${
                      whyMenu.firstWorseId
                        ? ` (${model.allRows.find((r) => r.row_id === whyMenu.firstWorseId)?.label ?? ""})`
                        : ""
                    }`}
                    secondary={`Side by side with ${baseLabel0}`}
                    primaryTypographyProps={{ typography: "s1" }}
                    secondaryTypographyProps={{ typography: "s3" }}
                  />
                </MenuItem>
              ) : null,
              whyMenu.worse > 0 ? (
                <MenuItem
                  key="filter"
                  onClick={() => {
                    const m = whyMenu;
                    setWhyMenu(null);
                    onDeltaRef.current?.(m.runId, "down", true);
                  }}
                >
                  <ListItemIcon>
                    <Iconify icon="mdi:filter-variant" width={18} />
                  </ListItemIcon>
                  <ListItemText
                    primary={`Show the ${fmtCount(whyMenu.worse, whyMenu.unit)} that got worse`}
                    primaryTypographyProps={{ typography: "s1" }}
                  />
                </MenuItem>
              ) : null,
              <MenuItem
                key="explain"
                onClick={() => {
                  const m = whyMenu;
                  setWhyMenu(null);
                  setWhy({ runId: m.runId, pos: m.pos });
                }}
              >
                <ListItemIcon>
                  <Iconify icon="mdi:lightbulb-on-outline" width={18} />
                </ListItemIcon>
                <ListItemText
                  primary="Explain"
                  secondary="The pattern Falcon found and the top causes"
                  primaryTypographyProps={{ typography: "s1" }}
                  secondaryTypographyProps={{ typography: "s3" }}
                />
              </MenuItem>,
            ].filter(Boolean)
          : null}
      </Menu>
      <Popover
        open={Boolean(why)}
        anchorEl={why?.pos ? undefined : why?.anchor}
        anchorReference={why?.pos ? "anchorPosition" : "anchorEl"}
        anchorPosition={why?.pos}
        onClose={() => setWhy(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { width: 560, maxWidth: "92vw", p: 1.5 } } }}
      >
        {why ? (
          <Stack gap={1} data-testid="matrix-why-panel">
            <Typography typography="s1" fontWeight="fontWeightSemiBold">
              {`Why ${runsById[why.runId]?.label ?? model.columnsById[why.runId]?.label ?? ""} moved vs ${baseLabel}`}
            </Typography>
            {whyCoverage ? (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="matrix-why-coverage"
              >
                {whyCoverage}
              </Typography>
            ) : null}
            {pattern && baselineId ? (
              <Box sx={{ mx: -1 }}>
                <PatternCallout
                  wrap
                  noun={verdictNoun}
                  pattern={pattern}
                  cause={patternCause}
                  runLabel={patternRun?.label ?? ""}
                  active={patternActive}
                  onShowRows={() =>
                    patternActive
                      ? setRowsPill(null)
                      : setRowsPill(pattern.rows, patternLabel)
                  }
                  onCompare={() => {
                    const rows = (matrix.rows ?? []).filter((r) =>
                      pattern.rows.includes(r.row_id),
                    );
                    const row =
                      rows.find((r) => r.source === "session") ?? rows[0];
                    if (row) openCell(row, pattern.run_id);
                  }}
                  onTestFix={
                    runsById[pattern.run_id] &&
                    !canIterate(runsById[pattern.run_id])
                      ? undefined
                      : () =>
                          navigate(
                            iterateHref(`${pathname}${search}`, {
                              runId: pattern.run_id,
                              scope: "rows",
                              rows: pattern.row_labels ?? pattern.rows,
                              change: patternCause?.id,
                              cause: patternCause?.id,
                            }),
                          )
                  }
                  testFixDisabledReason={
                    readOnly ? "Editors can start runs" : null
                  }
                />
              </Box>
            ) : null}
            {(data.rootCauses?.causes ?? [])
              .filter((c) => c.run_id === why.runId)
              .slice(0, 3)
              .map((c) => (
                <Stack
                  key={c.id}
                  gap={0.25}
                  sx={{
                    pl: 1,
                    borderLeft: "2px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography typography="s2" fontWeight="fontWeightMedium">
                    {whoFailsText(
                      c,
                      (id) =>
                        runsById[id]?.label ??
                        model.columnsById[id]?.label ??
                        "",
                    )}
                  </Typography>
                  {c.explanation || c.summary ? (
                    <Typography typography="s2" color="text.secondary">
                      {c.explanation ?? c.summary}
                    </Typography>
                  ) : null}
                  <Typography typography="s3" color="text.disabled">
                    {RULES_METHOD_LABEL}
                  </Typography>
                </Stack>
              ))}
            {firstWorse(why.runId) ? (
              <Button
                size="small"
                variant="outlined"
                startIcon={<Iconify icon="mdi:compare-horizontal" width={16} />}
                sx={{ alignSelf: "flex-start" }}
                onClick={() => {
                  const row = firstWorse(why.runId);
                  setWhy(null);
                  const href = sideBySideHref(row, {
                    runIds: [baselineId, why.runId].filter(Boolean),
                  });
                  const u = new URL(href, window.location.origin);
                  u.searchParams.set("rail", "worse");
                  u.searchParams.set("focus", why.runId);
                  navigate(`${u.pathname}${u.search}`);
                }}
              >
                Open the first failing task side by side
              </Button>
            ) : (
              <Typography typography="s2" color="text.secondary">
                No task got worse for this run.
              </Typography>
            )}
          </Stack>
        ) : null}
      </Popover>
      <Stack
        direction="row"
        sx={{ flex: 1, minHeight: 300, mx: 1, mb: 1, gap: 1 }}
      >
        <Box
          sx={{
            flex: 1,
            minWidth: 0,
            // Fills what is left of the window (no page scroll at 1280×720):
            // the bottom lines stay on screen under the rows.
            display: "flex",
            flexDirection: "column",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 0.5,
            overflow: "hidden",
          }}
        >
          <ResultDrawer
            datasetId={datasetId}
            view={drawerView}
            runs={runsInView}
            runsById={runsById}
            colors={colors}
            baselineId={baselineId}
            readOnly={readOnly}
            url={url}
            mode={mode}
            grid={grid}
          />
        </Box>
        {sidePanel ? (
          <Box
            data-testid="cost-vs-quality-panel"
            sx={{
              width: 400,
              flexShrink: 0,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              p: 1.25,
              overflowY: "auto",
            }}
          >
            <Typography
              typography="s1"
              fontWeight="fontWeightSemiBold"
              sx={{ mb: 0.5 }}
            >
              Cost vs quality
            </Typography>
            {pareto(() => setParetoOpen(false))}
          </Box>
        ) : null}
      </Stack>
      <MatrixHoverCard
        open={Boolean(hover)}
        anchorEl={hover?.anchor}
        row={hover?.row}
        runLabel={hover ? runsById[hover.runId]?.label ?? "" : ""}
        cell={hover?.row?.cells?.[hover?.runId]}
        metricNames={model.metricNames}
        metricDefs={model.metricDefs}
        cause={hoverCause}
        pairLabel={hoverPair ? runsById[hoverPair]?.label : null}
        onOpenTrajectory={() => hover && openCell(hover.row, hover.runId)}
        onCompare={() =>
          hover && openCell(hover.row, hover.runId, { pairWith: hoverPair })
        }
        onAddNote={() =>
          hover && navigate(noteDraftHref(hover.row, hover.runId))
        }
        addNoteDisabledReason={readOnly ? "Editors can add notes" : null}
        onEnter={() => clearTimeout(leaveTimer.current)}
        onLeave={onLeaveCell}
        call={
          hover
            ? voice?.calls?.[hover.row?.row_id]?.[hover.runId] ?? null
            : null
        }
      />
    </Stack>
  );
};

MatrixView.propTypes = {
  mode: PropTypes.oneOf(["experiment", "compare"]).isRequired,
  datasetId: PropTypes.string,
  experimentId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
};

export default MatrixView;
