// R2 stub (W0a) → filled by L4
import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  IconButton,
  LinearProgress,
  Skeleton,
  Stack,
  Typography,
  Chip,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useRunAnalysis } from "src/api/develop/analysis";
import { configKeyOf, useRunConfigs, useWorkbench } from "src/api/develop/r2";
import { canIterate } from "src/sections/experiment-detail/iterate/iterateModel";
import {
  fetchResultIds,
  runResultsKey,
  useRunResults,
} from "src/api/develop/run-results";
import { useQuery } from "@tanstack/react-query";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import { runColorAt, useRunColors } from "src/sections/common/Runs/runColors";
import {
  parsePills,
  rowsPill,
  samePill,
  serializePills,
} from "src/sections/common/Results/filterPills";
import {
  gapSlices,
  passAt1,
  runCells,
} from "src/sections/common/Analysis/runStats";
import {
  compareSummaryHref,
  iterateHref,
  matrixHref,
  resultsGridHref,
} from "src/sections/common/Analysis/analysisLinks";
import FilterMenu from "src/sections/experiment-detail/results/FilterMenu";
import FilterPills from "src/sections/experiment-detail/results/FilterPills";
import WorkbenchRail, { RAIL_W } from "./WorkbenchRail";
import PivotTable from "./PivotTable";
import GapSlices, { gapTitle, sliceLine } from "./GapSlices";
import SavedViewsMenu from "./SavedViewsMenu";
import ByDatasetView from "./ByDatasetView";
import DistributionChart from "./charts/DistributionChart";
import CostQualityChart from "./charts/CostQualityChart";
import GroupBarsChart from "./charts/GroupBarsChart";
import TrendChart from "./charts/TrendChart";
import HeatmapChart from "./charts/HeatmapChart";
import TwoRunScatterChart from "./charts/TwoRunScatterChart";
import DeltaBarsChart from "./charts/DeltaBarsChart";
import {
  COL_LABELS,
  WB_KEYS,
  groupLabel,
  groupOptions,
  matrixMeasureOf,
  defaultRowsAxis,
  measureOptions,
  exploreTabs,
  readExploreTab,
  readWorkbench,
} from "./workbenchModel";
import { FAB_GUTTER_PB } from "src/sections/common/ux/useFabGutter";
import {
  buildPivot,
  columnsFor,
  sortedRowKeys,
  weakestByColumn,
} from "./workbenchPivot";
import { spreadSentence } from "./workbenchStats";
import { trendPoints } from "./workbenchSeries";
import {
  pivotTableData,
  tableCsv,
  workbenchMarkdown,
} from "./workbenchMarkdown";
import { BY_AXES, defaultByAxis } from "./byDatasetModel";
import { fmtValue } from "./workbenchFormat";
import { useJudgeTrust, TrustLine } from "./JudgeTrust";
import { groupDeltas } from "./groupDelta";
import ExploreViewBar from "./ExploreViewBar";
import ConfigDiffView from "src/sections/common/CompareRuns/ConfigDiffView";
import ChartBoard from "./ChartBoard";
import BestOfView from "src/sections/common/BestOf/BestOfView";
import RunMetricsView from "./RunMetricsView";
import ClassificationView from "src/sections/common/DerivedMetrics/ClassificationView";
import MatchByKeyDialog from "src/sections/common/CompareRuns/MatchByKeyDialog";
import { hasW5 } from "./w5Api";
import SummaryPrintView from "src/sections/common/SummaryPrint/SummaryPrintView";
import { groupRowsPill, ownGroupPill, sameIds } from "./groupPill";
import { compileGroupExpression } from "./matrixScope";
import RunMetricPanel from "./RunMetricPanel";
import { isRunMetricKey, runMetricMeasures } from "./runMetricModel";
import { useDerivedMetrics } from "src/sections/common/DerivedMetrics/useDerivedMetrics";
import {
  customError,
  customGrouper,
  customNameOf,
  withCustomGroups,
} from "./customColumnGroups";
import useCustomColumns, {
  fetchCustomColumnValues,
} from "src/sections/common/CustomColumns/useCustomColumns";

const EDITORS_ONLY = "Editors can start runs";

function saveBlob(text, name) {
  const url = window.URL.createObjectURL(
    new Blob([text], { type: "text/csv" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.setAttribute("download", name);
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

function LoadingState() {
  return (
    <Stack
      direction="row"
      sx={{
        flex: 1,
        minHeight: 0,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Stack
        gap={1.5}
        sx={{ width: RAIL_W, p: 1.5, borderRight: 1, borderColor: "divider" }}
      >
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <Skeleton key={i} variant="rounded" height={i === 5 ? 32 : 40} />
        ))}
      </Stack>
      <Stack gap={1.5} sx={{ flex: 1, p: 1.5 }}>
        <Skeleton variant="rounded" height={300} />
        <Skeleton variant="rounded" height={240} />
      </Stack>
    </Stack>
  );
}

/**
 * The Workbench (R2-SPEC P4): pivot any measure by any two axes, five
 * charts, a pivot under every chart, `Where the gap is`, saved views;
 * `across=datasets` is By dataset. Every number is computed in the browser
 * from R3 `shape=matrix` (workbenchPivot.js) and opens exactly its rows.
 *
 * Props (frozen, R2 manifest row 16):
 * @param {string} props.datasetId
 * @param {string[]} props.runIds
 * @param {string|null} [props.baselineId]
 * @param {"experiment"|"compare"} [props.mode]
 */
const Workbench = ({
  datasetId,
  runIds = [],
  baselineId,
  mode = "compare",
}) => {
  const [sp, setSp] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { experimentId } = useParams();
  const { role } = useAuthContext();
  const canEdit = Boolean(
    RolePermission.DATASETS?.[PERMISSIONS.UPDATE]?.[role] ?? true,
  );
  const state = useMemo(() => readWorkbench(sp), [sp]);
  const filtersRaw = sp.get("filters") || null;
  const pills = useMemo(() => parsePills(filtersRaw), [filtersRaw]);
  const absolute = sp.get("baseline") === "none";
  const [highlight, setHighlight] = useState(null);

  const patch = useCallback(
    (obj) =>
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev);
          Object.entries(obj).forEach(([k, v]) => {
            if (v == null || v === "") next.delete(k);
            else next.set(k, String(v));
          });
          return next;
        },
        { replace: true },
      ),
    [setSp],
  );

  const analysisQ = useRunAnalysis(datasetId, {
    runIds,
    baselineId: absolute ? "none" : baselineId ?? undefined,
  });
  const analysis = analysisQ.data;
  const baseId =
    absolute || runIds.length < 2
      ? null
      : baselineId ?? analysis?.baseline_run_id ?? runIds[0];
  const configsQ = useRunConfigs(datasetId);
  const r1 = configsQ.data;
  const runsById = useMemo(() => {
    const map = Object.fromEntries(
      (analysis?.runs ?? []).map((r) => [r.run_id, r]),
    );
    (r1?.results ?? []).forEach((r) => {
      map[r.run_id] = { ...map[r.run_id], ...r };
    });
    return map;
  }, [r1, analysis]);
  const viewRuns = useMemo(
    () => runIds.map((id) => runsById[id]).filter(Boolean),
    [runIds, runsById],
  );
  const k = Math.max(1, ...viewRuns.map((r) => r.trials ?? 1));
  const metricDefs = useMemo(
    () =>
      (r1?.metric_defs ?? []).filter(
        (d) =>
          d.key.startsWith("check:") ||
          viewRuns.some((r) => (r.metric_keys ?? []).includes(d.key)),
      ),
    [r1, viewRuns],
  );
  const across = state.across;
  const matrixQ = useWorkbench(
    {
      datasetId,
      runIds,
      baselineId: baseId ?? undefined,
      measure: matrixMeasureOf(state.measure),
      filters: filtersRaw ?? undefined,
    },
    { enabled: across !== "datasets", placeholderData: (prev) => prev },
  );
  // The matrix read is unfiltered; the shared pills select rows through the
  // same R3 filter the grid uses, so a filtered count equals the grid's.
  const idsQ = useQuery({
    queryKey: [
      ...runResultsKey(datasetId),
      "wb-ids",
      runIds.join(","),
      baseId,
      filtersRaw,
    ],
    queryFn: () =>
      fetchResultIds(datasetId, {
        runIds,
        baselineId: baseId ?? undefined,
        pills,
      }),
    enabled:
      Boolean(datasetId) &&
      runIds.length > 0 &&
      pills.length > 0 &&
      across !== "datasets",
  });
  const r3 = useRunResults(
    datasetId,
    { runIds, baselineId: baseId ?? undefined },
    { enabled: across !== "datasets" },
  );
  const summary = r3.data?.summary;
  const matrix = useMemo(() => {
    const m = matrixQ.data;
    if (!m || !pills.length || !idsQ.data) return m;
    const keep = new Set(idsQ.data);
    return { ...m, rows: (m.rows ?? []).filter((r) => keep.has(r.row_id)) };
  }, [matrixQ.data, pills.length, idsQ.data]);
  // the interface contract: the board's cards keep the same filtered rows.
  const keepIds = useMemo(
    () => (pills.length && idsQ.data ? new Set(idsQ.data) : null),
    [pills.length, idsQ.data],
  );
  // R3: with no `rows=` in the URL the rows axis is the first dimension
  // that splits the tasks (Category), not one `All rows` cluster.
  const rowsAxis =
    sp.get("rows") || state.across === "datasets"
      ? state.rows
      : defaultRowsAxis(matrix);
  const railState = useMemo(
    () => (rowsAxis === state.rows ? state : { ...state, rows: rowsAxis }),
    [state, rowsAxis],
  );
  // the interface contract: Rows · Custom column ▸ <name> (custom result columns; the
  // option is absent until its endpoints are registered).
  const custom = useCustomColumns(datasetId, {
    enabled: state.across !== "datasets",
  });
  const customName = customNameOf(rowsAxis);
  const customQ = useQuery({
    queryKey: [
      "w5-custom-groups",
      datasetId,
      runIds.join(","),
      baselineId ?? null,
      customName,
    ],
    queryFn: () =>
      fetchCustomColumnValues(
        datasetId,
        { runIds, baselineId },
        { columnIds: [customName] },
      ),
    enabled: Boolean(customName && custom.available && runIds.length),
  });
  const customGroups = useMemo(
    () =>
      customName && customQ.data
        ? customGrouper(customQ.data, customName)
        : null,
    [customName, customQ.data],
  );
  const hasTurns = useMemo(
    () =>
      (matrix?.rows ?? []).some((r) =>
        Object.values(r.cells ?? {}).some((c) =>
          (c.trace_summary ?? []).some((t) => t?.turns != null),
        ),
      ),
    [matrix],
  );
  const measures = useMemo(
    () => measureOptions(metricDefs, { k, hasTurns }),
    [metricDefs, k, hasTurns],
  );
  const measure =
    measures.find((m) => m.key === state.measure && !m.disabled) ?? measures[0];
  // the interface contract: the rail's Measure also lists the dataset's run metrics
  // (`rm:<id>`); the pivot keeps its own measures.
  const runMetricsQ = useDerivedMetrics(
    hasW5("derivedMetrics") ? datasetId : null,
    runIds,
  );
  const railMeasures = useMemo(
    () => [...measures, ...runMetricMeasures(runMetricsQ.data?.metrics)],
    [measures, runMetricsQ.data],
  );
  const runMetricKey = isRunMetricKey(state.measure) ? state.measure : null;
  const trust = useJudgeTrust(datasetId, measure);

  const runColors = useRunColors(runIds, baseId);
  const columns = useMemo(
    () =>
      columnsFor(viewRuns, state.cols).map((c) => {
        const run = c.runIds.length === 1 ? runsById[c.runIds[0]] : null;
        return {
          ...c,
          title: run
            ? `${run.label} ${run.name ?? ""}`
            : c.runIds.map((id) => runsById[id]?.label).join(", "),
          sub:
            state.cols === "run"
              ? run?.name
              : state.cols === "iteration"
                ? null
                : c.runIds.length > 1
                  ? `${c.runIds.length} runs`
                  : runsById[c.runIds[0]]?.label,
        };
      }),
    [viewRuns, state.cols, runsById],
  );
  const colColors = useMemo(
    () =>
      state.cols === "run" || state.cols === "iteration"
        ? Object.fromEntries(
            columns.map((c) => [c.key, runColors[c.runIds[0]]]),
          )
        : Object.fromEntries(columns.map((c, i) => [c.key, runColorAt(i)])),
    [columns, runColors, state.cols],
  );
  const isCheck = useCallback(
    (key) =>
      metricDefs.some((d) => d.key === `check:${key}`) ||
      !metricDefs.some((d) => d.key === `eval:${key}`),
    [metricDefs],
  );
  const pivot = useMemo(
    () =>
      matrix && columns.length && (!customName || customGroups)
        ? buildPivot(matrix, {
            rows: rowsAxis,
            columns,
            measure,
            agg: state.agg,
            k,
            isCheck,
            ctx: customGroups ? { customGroups } : undefined,
          })
        : null,
    [
      matrix,
      columns,
      measure,
      rowsAxis,
      state.agg,
      k,
      isCheck,
      customName,
      customGroups,
    ],
  );
  const groups = useMemo(
    () => sortedRowKeys(pivot, state.sort),
    [pivot, state.sort],
  );
  const baseCol = baseId
    ? columns.find((c) => c.runIds.includes(baseId))?.key ?? null
    : null;
  const weakest = useMemo(() => weakestByColumn(pivot), [pivot]);
  // the interface contract: Δ vs the baseline per group × run (paired, with its interval).
  const deltas = useMemo(
    () =>
      pivot && baseId && (measure.kind === "rate" || state.agg === "avg")
        ? groupDeltas(matrix, pivot, { baselineId: baseId, measure })
        : {},
    [matrix, pivot, baseId, measure, state.agg],
  );
  const sentence =
    rowsAxis === "row"
      ? null
      : spreadSentence(pivot, measure, {
          baseCol: baseCol ?? undefined,
          baseLabel: baseCol
            ? columns.find((c) => c.key === baseCol)?.label
            : undefined,
        });

  // ---------------------------------------------------------- the gap
  const candidates = viewRuns.filter((r) => r.run_id !== baseId);
  const candId = useMemo(() => {
    if (state.cand && candidates.some((c) => c.run_id === state.cand))
      return state.cand;
    if (!matrix || !baseId) return candidates[0]?.run_id ?? null;
    const base = passAt1(runCells(matrix, baseId)).rate ?? 0;
    let best = null;
    candidates.forEach((c) => {
      const d = Math.abs(
        (passAt1(runCells(matrix, c.run_id)).rate ?? base) - base,
      );
      if (!best || d > best.d) best = { id: c.run_id, d };
    });
    return best?.id ?? null;
  }, [state.cand, candidates, matrix, baseId]);
  const showGap =
    across === "runs" && baseId && candId && !sp.get("rows") && matrix;
  const slices = useMemo(
    () => (showGap ? gapSlices(matrix, baseId, candId) : []),
    [showGap, matrix, baseId, candId],
  );
  const label = (id) => runsById[id]?.label ?? "this run";

  // ---------------------------------------------------------- links
  const here = `${location.pathname}${location.search}`;
  const gridHref = useCallback(
    (extra = []) =>
      resultsGridHref({
        mode,
        datasetId,
        experimentId,
        runIds,
        baselineId: baseId,
        pills: [...pills, ...extra],
      }),
    [mode, datasetId, experimentId, runIds, baseId, pills],
  );
  const openRows = (ids, text) =>
    navigate(gridHref([rowsPill(ids, { label: text })]));
  // the interface contract: a group opens the results with the group's own pill (metadata /
  // split) when that pill selects exactly the group's rows; else its rows.
  const openGroup = async (g) => {
    const ids = pivot?.rowTotals?.[g]?.rowIds ?? [];
    const own = ownGroupPill(rowsAxis, g, summary);
    let pill = groupRowsPill(rowsAxis, g, ids);
    if (own) {
      try {
        const got = await fetchResultIds(datasetId, {
          runIds,
          baselineId: baseId ?? undefined,
          pills: [...pills, own],
        });
        if (sameIds(got, ids)) pill = own;
      } catch {
        // keep the rows pill: it selects exactly the group's rows
      }
    }
    navigate(gridHref([pill]));
  };
  const iterateRun = candId ?? baseId ?? runIds[0];
  // dataset integration FE-0: Test a fix only on a prompt or agent run.
  const iterateRunRow = (r1?.results ?? []).find(
    (r) => r.run_id === iterateRun,
  );
  const canIterateRun = !iterateRunRow || canIterate(iterateRunRow);
  const iterateSlice = (ids, text) =>
    navigate(
      iterateHref(gridHref([rowsPill(ids, { label: text })]), {
        runId: iterateRun,
        scope: "filter",
      }),
    );
  const rowsTitle = groupLabel(rowsAxis);
  const onOpenCell = (g, col, cell) => {
    const fails = measure.kind === "rate" || pivot?.failedEval;
    const ids =
      fails && cell.failRowIds?.length ? cell.failRowIds : cell.rowIds;
    const what = rowsAxis === "none" ? "" : `${g} · `;
    const text = pivot?.failedEval
      ? `Failed eval ${g} · ${col.label}`
      : fails && cell.failRowIds?.length
        ? `${what}${col.label} fails`
        : `${what}${col.label}`;
    openRows(ids, text);
  };
  const openInMatrix = (g, col, cell) =>
    navigate(
      matrixHref({
        mode,
        datasetId,
        experimentId,
        runIds,
        baselineId: baseId,
        pills: [
          ...pills,
          rowsPill(cell.rowIds, { label: `${rowsTitle}: ${g}` }),
        ],
        from: here,
      }),
    );
  const groupActions = (g) =>
    rowsAxis === "none"
      ? []
      : [
          {
            label: "View rows",
            icon: "mdi:table-eye",
            onClick: () =>
              openRows(
                pivot.rowTotals[g].rowIds,
                pivot.failedEval ? `Failed eval ${g}` : `${rowsTitle}: ${g}`,
              ),
          },
          ...(canIterateRun
            ? [
                {
                  label: "Test a fix on this slice…",
                  icon: "mdi:sync",
                  disabled: canEdit ? null : EDITORS_ONLY,
                  onClick: () =>
                    iterateSlice(
                      pivot.rowTotals[g].rowIds,
                      pivot.failedEval
                        ? `Failed eval ${g}`
                        : `${rowsTitle}: ${g}`,
                    ),
                },
              ]
            : []),
        ];

  // ---------------------------------------------------------- footer
  const datasetsQ = useDevelopDatasetList();
  const dataset = (datasetsQ.data ?? []).find((d) => d.datasetId === datasetId);
  const viewName = `${rowsAxis === "failed_eval" ? "failed attempts" : measure?.short ?? "pass@1"}${rowsAxis !== "none" ? ` by ${rowsTitle.toLowerCase()}` : ""} · ${(COL_LABELS[state.cols] ?? "Runs").toLowerCase()}`;
  const table = pivot
    ? pivotTableData(pivot, groups, measure, { groupTitle: rowsTitle })
    : null;
  const copyLink = async () => {
    await copyToClipboard(window.location.href);
    enqueueSnackbar("Link copied", { variant: "success" });
  };
  const copyMarkdown = async () => {
    const md = workbenchMarkdown({
      dataset: dataset?.name ?? "Dataset",
      runs: viewRuns.map((r) => r.label),
      measure:
        across === "datasets" ? "Headline measure by dataset" : measure.label,
      sentence: across === "datasets" ? null : sentence,
      gap:
        showGap && slices.length
          ? {
              title: gapTitle(slices, label(candId), label(baseId)),
              lines: slices.map(sliceLine),
            }
          : null,
      table: showGap && slices.length ? null : table,
      link: window.location.href,
    });
    await copyToClipboard(md);
    enqueueSnackbar("Markdown copied", { variant: "success" });
  };
  const exportCsv = () => {
    if (!table) return;
    saveBlob(
      tableCsv(table),
      `workbench-${viewName.replace(/[^a-z0-9@^]+/gi, "-")}.csv`,
    );
    enqueueSnackbar("CSV downloaded", { variant: "success" });
  };
  const reset = () => {
    setHighlight(null);
    patch(Object.fromEntries(WB_KEYS.map((key) => [key, null])));
  };
  const display = {
    view: "workbench",
    measure: state.measure,
    agg: state.agg,
    rows: rowsAxis,
    cols: state.cols,
    chart: state.chart,
    baseline: sp.get("baseline") === "none" ? "none" : null,
    across,
    filters: filtersRaw,
    // the interface contract: the chart board travels with the view.
    board: sp.get("board") || null,
    wbtab: sp.get("wbtab") || null,
  };
  const applyView = (d, name) => {
    setHighlight(null);
    patch({
      measure: d.measure && d.measure !== "pass_at_1" ? d.measure : null,
      agg: d.agg && d.agg !== "avg" ? d.agg : null,
      rows: d.rows && d.rows !== "none" ? d.rows : null,
      cols: d.cols && d.cols !== "run" ? d.cols : null,
      chart: d.chart ?? null,
      across: d.across && d.across !== "runs" ? d.across : null,
      ...(d.baseline === "none" ? { baseline: "none" } : {}),
      ...(d.filters ? { filters: d.filters } : {}),
      board: d.board ?? null,
      wbtab: d.wbtab ?? null,
    });
    enqueueSnackbar(`Applied “${name}”`, { variant: "success" });
  };
  const iconBtn = (title, icon, onClick, disabled) => (
    <CustomTooltip show title={title} size="small" placement="top" arrow>
      <span>
        <IconButton
          size="small"
          aria-label={title}
          onClick={onClick}
          disabled={disabled}
        >
          <Iconify icon={icon} width={18} />
        </IconButton>
      </span>
    </CustomTooltip>
  );
  const footer = (
    <Stack gap={0.75}>
      <SavedViewsMenu
        datasetId={datasetId}
        display={display}
        defaultName={viewName}
        onApply={applyView}
      />
      <Stack direction="row" justifyContent="space-between">
        {iconBtn("Copy link to this pivot", "mdi:link-variant", copyLink)}
        {iconBtn(
          "Copy the table as Markdown",
          "mdi:language-markdown-outline",
          copyMarkdown,
          !pivot && across !== "datasets",
        )}
        {iconBtn(
          "Export the table as CSV",
          "mdi:file-delimited-outline",
          exportCsv,
          !table || across === "datasets",
        )}
        {iconBtn("Reset to the default pivot", "mdi:restore", reset)}
      </Stack>
    </Stack>
  );

  // ---------------------------------------------------------- rail
  // R2D-02: All datasets is about the BASELINE agent (the one you ship);
  // `xfocus=cand` switches it to the candidate. Runs-across keeps the
  // candidate focus.
  const xfocusCand = sp.get("xfocus") === "cand";
  const candRun = runsById[state.cand ?? ""] ?? null;
  const baseRun = runsById[baseId ?? ""] ?? null;
  const focusRun =
    across === "datasets"
      ? (xfocusCand ? candRun : null) ??
        baseRun ??
        candRun ??
        viewRuns[0] ??
        null
      : candRun ?? baseRun ?? viewRuns[0] ?? null;
  const otherFocus =
    across === "datasets" && candRun && baseRun && candRun !== baseRun
      ? focusRun === baseRun
        ? candRun
        : baseRun
      : null;
  const byAxis =
    state.byCols && BY_AXES[state.byCols]
      ? state.byCols
      : defaultByAxis(focusRun);
  const chartDisabled =
    across === "datasets"
      ? Object.fromEntries(
          ["distribution", "cost_quality", "trend", "heatmap"].map((c) => [
            c,
            "Needs rows from one dataset",
          ]),
        )
      : {
          trend: columns.length < 2 ? "Needs 2 or more columns" : null,
          heatmap: pivot?.failedEval ? "Needs a measure, not counts" : null,
          // the interface contract: both read one baseline and one candidate run.
          scatter2:
            !baseId || !candId
              ? "Needs a baseline and a candidate run"
              : state.cols !== "run"
                ? "Needs Columns: Runs"
                : pivot?.failedEval
                  ? "Needs a measure, not counts"
                  : null,
          delta_bars:
            !baseId || !candId ? "Needs a baseline and a candidate run" : null,
        };
  const chart =
    across === "datasets"
      ? "bars"
      : chartDisabled[state.chart]
        ? "bars"
        : state.chart;
  const filterView = {
    runs: viewRuns,
    runCount: viewRuns.length,
    baselineId: baseId,
    metrics: summary?.metrics ?? metricDefs,
    humanMetrics: summary?.human_metrics ?? [],
    splits: summary?.splits ?? [],
    metadataKeys: summary?.metadata_keys ?? [],
    metadataValues: summary?.metadata_values ?? {},
    hasTrials: k > 1,
    hasPreference: Object.keys(summary?.preference ?? {}).length > 0,
    hasAgent: viewRuns.some(
      (r) => r.kind === "agent" || r.target_type === "agent",
    ),
    candidateId: candId,
  };
  const truncated = matrix && matrix.total > (matrix.rows?.length ?? 0);
  const note = truncated ? (
    <Alert severity="info" sx={{ py: 0, mb: 1, typography: "s3" }}>
      Showing the first {(matrix.rows?.length ?? 0).toLocaleString("en-US")}{" "}
      rows
    </Alert>
  ) : null;

  const loading =
    analysisQ.isLoading ||
    configsQ.isLoading ||
    (across !== "datasets" &&
      (matrixQ.isLoading || (pills.length > 0 && idsQ.isLoading)));
  const error =
    analysisQ.isError ||
    configsQ.isError ||
    (across !== "datasets" && matrixQ.isError);

  // the interface contract / B8: Explore's tabs (Pivot & charts · Config) and ⋯ Print.
  const tab = readExploreTab(sp, exploreTabs(hasW5));
  const printOpen = sp.get("print") === "1";
  const matchOpen = sp.get("match") === "1";
  const header = (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      flexWrap="wrap"
      sx={{ minHeight: 36 }}
    >
      {across !== "datasets" && (
        <ExploreViewBar
          tab={tab}
          onTab={(v) => patch({ wbtab: v === "pivot" ? null : v })}
          onPrint={() => patch({ print: "1" })}
          onMatch={() => patch({ match: "1" })}
        />
      )}
      {/* IT-R3 G6 P3: no Overview | Workbench switch (one view); the
          Workbench lives in the Summary's Explore sheet. */}
      {across !== "datasets" && (
        <FilterMenu
          view={filterView}
          onAdd={(pill) => patch({ filters: serializePills([...pills, pill]) })}
        />
      )}
      <FilterPills
        pills={pills}
        runs={viewRuns}
        metrics={metricDefs}
        baselineId={baseId}
        total={pills.length ? idsQ.data?.length : matrix?.rows?.length}
        examplesTotal={analysis?.examples_total}
        onRemove={(pill) =>
          patch({
            filters:
              serializePills(pills.filter((p) => !samePill(p, pill))) || null,
          })
        }
        onClear={() => patch({ filters: null })}
      />
      {(matrixQ.isFetching || analysisQ.isFetching) && !loading && (
        <Typography typography="s3" color="text.disabled">
          Updating…
        </Typography>
      )}
    </Stack>
  );

  if (error)
    return (
      <Box sx={{ p: 1.5 }}>
        {header}
        <Alert
          severity="error"
          sx={{ mt: 1.5 }}
          action={
            <Button
              color="inherit"
              size="small"
              onClick={() => {
                analysisQ.refetch();
                configsQ.refetch();
                matrixQ.refetch();
              }}
            >
              Retry
            </Button>
          }
        >
          The workbench could not be loaded.
        </Alert>
      </Box>
    );

  const chartTitle = viewName.replace(/^./, (c) => c.toUpperCase());
  const axisTitle =
    measure.kind === "rate" ? `${measure.short} (%)` : measure.label;
  const paired = rowsAxis !== "none" && columns.length === 2;
  // A Trend hop opens the two points' runs on the Summary.
  const onHop = (a, b) =>
    navigate(
      compareSummaryHref({
        datasetId,
        runIds: [...a.col.runIds, ...b.col.runIds],
        baselineId: a.col.runIds[0],
        from: here,
      }),
    );
  const renderChart = () => {
    if (!pivot) return null;
    if (chart === "distribution")
      return (
        <DistributionChart
          matrix={matrix}
          columns={columns}
          measure={pivot.failedEval ? measures[0] : measure}
          colColors={colColors}
          onOpenRows={openRows}
          title={`Distribution · ${measure.label}`}
        />
      );
    if (chart === "cost_quality")
      return (
        <CostQualityChart
          matrix={matrix}
          pivot={pivot}
          columns={columns}
          measure={measure}
          colColors={colColors}
          paired={paired}
          baseCol={baseCol}
          onOpenRows={openRows}
          onOpenColumn={(col) =>
            onOpenCell("All rows", col, pivot.colTotals[col.key])
          }
          title={
            paired
              ? `${columns.find((c) => c.key !== (baseCol ?? columns[0].key))?.label} vs ${columns.find((c) => c.key === (baseCol ?? columns[0].key))?.label}, row by row`
              : "Cost vs quality"
          }
        />
      );
    if (chart === "trend") {
      const points = trendPoints(pivot, { axis: state.cols, runsById });
      return (
        <TrendChart
          points={points}
          measure={measure}
          colColors={colColors}
          title={`Trend · ${measure.label}`}
          axisTitle={axisTitle}
          caption={
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              {points
                .map(
                  (p) =>
                    `${p.label} ${fmtValue(measure, p.cell?.value, { precise: true })}${p.hop && /rerun/.test(p.hop) ? ` (${p.hop})` : ""}`,
                )
                .join(" → ")}
            </Typography>
          }
          onHop={onHop}
        />
      );
    }
    if (chart === "scatter2") {
      const baseColumn = columns.find((c) => c.key === baseCol);
      const candColumns = columns.filter(
        (c) => c.runIds.length === 1 && c.key !== baseCol,
      );
      return (
        <TwoRunScatterChart
          matrix={matrix}
          measure={measure}
          baseCol={baseColumn}
          candCol={
            candColumns.find((c) => c.runIds[0] === candId) ?? candColumns[0]
          }
          candidates={candColumns}
          onCand={(key) => patch({ cand: key })}
          colColors={colColors}
          onOpenRows={openRows}
        />
      );
    }
    if (chart === "delta_bars")
      return (
        <DeltaBarsChart
          analysis={analysis}
          candidateId={candId}
          candidates={candidates.map((c) => ({ id: c.run_id, label: c.label }))}
          onCand={(id) => patch({ cand: id })}
          baseLabel={label(baseId)}
          candLabel={label(candId)}
        />
      );
    if (chart === "heatmap")
      return (
        <HeatmapChart
          pivot={pivot}
          groups={groups}
          measure={measure}
          onOpenCell={openInMatrix}
          title={`Heatmap · ${chartTitle}`}
        />
      );
    return (
      <GroupBarsChart
        pivot={pivot}
        groups={groups}
        measure={measure}
        colColors={colColors}
        onOpenCell={onOpenCell}
        title={chartTitle}
        axisTitle={axisTitle}
      />
    );
  };

  return (
    <Stack
      sx={{
        p: 1.5,
        // The Falcon bubble sits bottom-right: keep its gutter so it never
        // covers the last column's delta or the last row.
        pr: `calc(12px + ${FAB_GUTTER_PB})`,
        pb: `calc(12px + ${FAB_GUTTER_PB})`,
        gap: 1,
        flex: 1,
        minHeight: 0,
      }}
    >
      {header}
      {printOpen && !loading && (
        <SummaryPrintView
          datasetName={dataset?.name ?? "Dataset"}
          analysis={analysis}
          runs={viewRuns}
          baselineId={baseId}
          link={window.location.href.replace(/([?&])print=1&?/, "$1")}
          onClose={() => patch({ print: null })}
        />
      )}
      {matchOpen && viewRuns.length > 0 && (
        <MatchByKeyDialog
          open
          onClose={() => patch({ match: null, match_b: null })}
          runs={viewRuns}
          defaultRunId={
            (across === "datasets" ? focusRun?.run_id : null) ??
            baseId ??
            runIds[0]
          }
          defaultOtherRunId={sp.get("match_b")}
        />
      )}
      {loading ? (
        <LoadingState />
      ) : tab === "board" && across !== "datasets" ? (
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <ChartBoard
            value={sp.get("board") || ""}
            onChange={(v) => patch({ board: v })}
            datasetId={datasetId}
            runIds={runIds}
            baselineId={baseId}
            filtersRaw={filtersRaw}
            keepIds={keepIds}
            columns={columns}
            colColors={colColors}
            measures={measures}
            groups={withCustomGroups(
              groupOptions(matrix, { across }),
              across === "datasets" ? [] : custom.columns,
            )}
            k={k}
            isCheck={isCheck}
            onOpenRows={openRows}
            colsAxis={state.cols}
            runsById={runsById}
            onHop={onHop}
            analysis={analysis}
            candidateId={candId}
            candidates={candidates.map((c) => ({
              id: c.run_id,
              label: c.label,
            }))}
            labelOf={label}
            savedViews={
              <Box sx={{ width: 260 }}>
                <SavedViewsMenu
                  datasetId={datasetId}
                  display={display}
                  defaultName={viewName}
                  onApply={applyView}
                />
              </Box>
            }
          />
        </Box>
      ) : tab === "run_metrics" && across !== "datasets" ? (
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <RunMetricsView
            datasetId={datasetId}
            runs={viewRuns}
            metricDefs={r1?.metric_defs ?? []}
          />
        </Box>
      ) : tab === "classification" && across !== "datasets" ? (
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <ClassificationView
            datasetId={datasetId}
            runs={viewRuns}
            onOpenRows={openRows}
          />
        </Box>
      ) : tab === "best_of" && across !== "datasets" ? (
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <BestOfView
            datasetId={datasetId}
            runs={viewRuns}
            keepIds={keepIds}
            runColors={runColors}
            onOpenRows={openRows}
          />
        </Box>
      ) : tab === "config" && across !== "datasets" ? (
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          <ConfigDiffView
            runs={viewRuns}
            defaultA={baseId ?? runIds[0]}
            defaultB={
              candId ?? runIds.find((id) => id !== (baseId ?? runIds[0]))
            }
          />
        </Box>
      ) : (
        <Stack
          direction="row"
          sx={{
            flex: 1,
            minHeight: 520,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            overflow: "hidden",
            bgcolor: "background.default",
          }}
        >
          <WorkbenchRail
            state={railState}
            onChange={(p) => {
              if ("rows" in p) setHighlight(null);
              patch(p);
            }}
            measures={railMeasures}
            measure={
              (runMetricKey &&
                railMeasures.find((m) => m.key === runMetricKey)) ||
              measure
            }
            trust={
              trust ? (
                <TrustLine
                  trust={trust}
                  datasetId={datasetId}
                  runId={baseId ?? runIds[0]}
                  rowIds={(matrix?.rows ?? []).map((r) => r.row_id)}
                />
              ) : null
            }
            groups={withCustomGroups(
              groupOptions(matrix, { across }),
              across === "datasets" ? [] : custom.columns,
            )}
            columnOptions={
              across === "datasets"
                ? Object.entries(BY_AXES).map(([key, l]) => ({ key, label: l }))
                : Object.entries(COL_LABELS).map(([key, l]) => ({
                    key,
                    label: l,
                  }))
            }
            chartDisabled={chartDisabled}
            pathColumns={Object.keys(matrix?.rows?.[0]?.values ?? {})}
            exprCheck={(text) =>
              compileGroupExpression(text, matrix).error?.message ?? null
            }
            sampleRow={matrix?.rows?.[0]?.values}
            datasetId={datasetId}
            runsInView={analysis?.runs ?? []}
            baselineValue={baseId ?? "none"}
            pinnedRunId={analysis?.dataset_baseline?.run_id ?? null}
            restrictTo={mode === "experiment" ? runIds : undefined}
            onBaseline={(id) => patch({ baseline: id })}
            across={across}
            byAxis={byAxis}
            note={note}
            footer={footer}
            key={`${across}`}
          />
          <Box sx={{ flex: 1, minWidth: 0, overflowY: "auto", p: 1.5 }}>
            {matrixQ.isFetching && across !== "datasets" && (
              <LinearProgress sx={{ height: 2, mb: 1 }} />
            )}
            {across === "datasets" && focusRun && otherFocus ? (
              <Stack direction="row" sx={{ mb: 1 }}>
                <Chip
                  size="small"
                  variant="outlined"
                  data-testid="by-dataset-focus"
                  icon={<Iconify icon="mdi:swap-horizontal" width={16} />}
                  label={`Showing ${focusRun.label}'s agent · switch to ${otherFocus.label}`}
                  onClick={() =>
                    patch({
                      xfocus: otherFocus === candRun ? "cand" : null,
                      config: null,
                    })
                  }
                  sx={{ typography: "s2", height: 28 }}
                />
              </Stack>
            ) : null}
            {across === "datasets" ? (
              <ByDatasetView
                focusRun={focusRun}
                configKey={state.config ?? configKeyOf(focusRun)}
                axis={byAxis}
                extraKeys={state.extraConfigs}
                onExtraKeys={(keys) => patch({ xcfg: keys.join("|") || null })}
                colColors={(i) => runColorAt(i)}
                onMatchCell={
                  hasW5("resultsMatch")
                    ? (runId) => patch({ match: "1", match_b: runId })
                    : undefined
                }
              />
            ) : runMetricKey ? (
              <RunMetricPanel
                datasetId={datasetId}
                runs={viewRuns}
                metricKey={runMetricKey}
                colColors={Object.fromEntries(
                  viewRuns.map((r) => [r.run_id, runColors?.[r.run_id]]),
                )}
              />
            ) : (
              <Stack gap={1.5}>
                {customName && customError(customQ.data, customName) && (
                  <Alert
                    severity="warning"
                    sx={{ py: 0 }}
                    data-testid="custom-column-error"
                  >
                    {`Custom column ${customName} could not be computed: ${customError(customQ.data, customName)}`}
                  </Alert>
                )}
                {state.cols === "iteration" && columns.length === 0 && (
                  <Alert severity="info" sx={{ py: 0 }}>
                    No run here has iterations. Iterate a run, then compare the
                    chain.
                  </Alert>
                )}
                {showGap && (
                  <GapSlices
                    slices={slices}
                    candLabel={label(candId)}
                    baseLabel={label(baseId)}
                    candidates={candidates}
                    onCand={(id) => patch({ cand: id })}
                    onPick={(s) => {
                      setHighlight(s.value);
                      patch({
                        rows: ["category", "difficulty", "split"].includes(
                          s.key,
                        )
                          ? `meta:${s.key}`
                          : s.key === "source"
                            ? "source"
                            : `meta:${s.key}`,
                      });
                    }}
                    onShowRows={(s) =>
                      openRows(s.row_ids, `${s.key} ${s.value}`)
                    }
                    onIterate={
                      canIterateRun
                        ? (s) => iterateSlice(s.row_ids, `${s.key} ${s.value}`)
                        : undefined
                    }
                    iterateDisabled={canEdit ? null : EDITORS_ONLY}
                  />
                )}
                {renderChart()}
                <PivotTable
                  pivot={pivot}
                  measure={pivot?.failedEval ? pivot.measure : measure}
                  groupTitle={rowsAxis === "none" ? "Rows" : rowsTitle}
                  colColors={colColors}
                  baselineCol={baseCol}
                  weakest={weakest}
                  sentence={
                    pivot?.failedEval
                      ? "Failed attempts by their first failed eval (Checks first)."
                      : sentence
                  }
                  sort={state.sort}
                  onSort={(v) =>
                    patch({ sort: v === "failing" ? "failing" : null })
                  }
                  transpose={state.transpose}
                  onTranspose={() =>
                    patch({ transpose: state.transpose ? null : "1" })
                  }
                  highlight={highlight}
                  onOpenCell={onOpenCell}
                  groupActions={groupActions}
                  deltas={deltas}
                  baselineLabel={baseId ? label(baseId) : undefined}
                  onOpenGroup={
                    rowsAxis === "none" || rowsAxis === "row"
                      ? undefined
                      : openGroup
                  }
                  emptyText={
                    pills.length
                      ? "No rows in this group."
                      : "No scored rows yet."
                  }
                />
              </Stack>
            )}
          </Box>
        </Stack>
      )}
    </Stack>
  );
};

Workbench.propTypes = {
  datasetId: PropTypes.string,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  mode: PropTypes.oneOf(["experiment", "compare"]),
};

export default Workbench;
