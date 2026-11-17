import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  runLedgerParts,
  spendLine,
  spendSplitText,
} from "src/sections/common/Runs/runLedger";
import { rowsPill } from "src/sections/common/Results/filterPills";

// W1-6: what a run spent, tries and judge calls (R1 `actual`),
// else the tries' total the analysis read.
const spentOf = (d) =>
  Number.isFinite(Number(d?.actual?.usd)) && d?.actual?.usd != null
    ? Number(d.actual.usd)
    : d?.cost_total ?? null;
const spendTip = (d) =>
  [spendSplitText(d?.actual), spendLine(d)].filter(Boolean).join(". ") || null;
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  alpha,
  Box,
  Button,
  FormControlLabel,
  Link,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import DeltaBadge from "src/sections/common/Metrics/DeltaBadge";
import {
  formatDelta,
  formatValue,
} from "src/sections/common/Metrics/metricFormat";
import { getRunStatus } from "src/sections/common/Runs/runStatus";
import RunStatusChip from "src/sections/common/Runs/RunStatusChip";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";
import { aggOf } from "src/sections/common/Analysis/MetricCard";
import {
  NUM_SX,
  EMPTY,
  fmtCost,
  fmtMs,
  fmtPct,
} from "src/sections/common/format/fmt";
import {
  ColumnsMenu,
  HeaderMenuButton,
  useAgColumnShaping,
  useColumnLayout,
  useLayoutPrefs,
} from "src/sections/common/ux/oneview";
import Term from "src/sections/common/ux/Term";
import CopyButton from "src/sections/common/ux/CopyButton";
import KeyHintTooltip from "src/sections/common/ux/KeyHintTooltip";
import useScrollEdges from "src/sections/common/ux/useScrollEdges";
import ExperimentSummaryTable from "../ExperimentSummaryTable";
import SpiderChartExperiment from "../ExperimentSummaryChart/SpiderChartExperiment";
import RankWithIndexRenderer from "../CustomRenderers/RankWithIndexRenderer";
import {
  allRunsCatalog,
  costText,
  deltaInfo,
  HEADER_HELP,
  passesEveryEvalText,
  reliableText,
  successCi,
  successText,
  timeText,
  tokensText,
  allRunsChartWord,
} from "./allRunsModel";

const cellBox = {
  display: "flex",
  alignItems: "center",
  height: "100%",
  gap: 0.75,
  minWidth: 0,
};

const TONE_FG = {
  good: (t) =>
    t.palette.mode === "dark"
      ? t.palette.success.light
      : t.palette.success.dark,
  bad: (t) =>
    t.palette.mode === "dark" ? t.palette.error.light : t.palette.error.dark,
  neutral: (t) => t.palette.text.secondary,
};

/** Term's look (1 px dotted underline) for a header with a definition. */
const DEFINED_SX = {
  cursor: "inherit",
  textDecorationLine: "underline",
  textDecorationStyle: "dotted",
  textDecorationThickness: "1px",
  textUnderlineOffset: "3px",
  textDecorationColor: (t) => t.palette.text.disabled,
  "&:hover, &[aria-expanded='true']": {
    textDecorationColor: (t) => t.palette.text.secondary,
  },
};

/**
 * Column header (AS#7, AS#8): label · aggregation caption; the label is a
 * hover definition (glossary Term, or the column's own definition with its
 * direction) — no ⓘ icons; click sorts; right-click or the ⋮ that appears
 * on hover / focus opens the header menu (sort, freeze, move, hide).
 */
function HeaderCell(props) {
  const {
    displayName,
    column,
    progressSort,
    enableSorting,
    term,
    agg,
    tip,
    showColumnMenu,
  } = props;
  const [sort, setSort] = useState(column.getSort());
  useEffect(() => {
    const on = () => setSort(column.getSort());
    column.addEventListener("sortChanged", on);
    return () => column.removeEventListener("sortChanged", on);
  }, [column]);
  const right = column.getColDef().type === "numericColumn";
  const stop = (e) => e.stopPropagation();
  // Glossary words keep their Term card (click pins it); every other
  // definition is hover-only, so clicking the label still sorts.
  const label = term ? (
    <Box component="span" onClick={stop} onKeyDown={stop}>
      <Term k={term} extra={tip}>
        {displayName}
      </Term>
    </Box>
  ) : tip ? (
    <CustomTooltip show size="small" placement="top" title={tip}>
      <Box component="span" sx={DEFINED_SX}>
        {displayName}
      </Box>
    </CustomTooltip>
  ) : (
    displayName
  );
  return (
    <Stack
      className="fa-header-cell"
      direction="row"
      alignItems="center"
      gap={0.5}
      onClick={(e) => enableSorting && progressSort(e.shiftKey)}
      // Right-click = the Excel header menu (sort, freeze, move, hide).
      onContextMenu={(e) => {
        if (!showColumnMenu) return;
        e.preventDefault();
        showColumnMenu(e.currentTarget);
      }}
      // L2 G2 (a11y #13): ag-grid's own columnheader carries aria-sort; a
      // second one on this inner div is not allowed (aria-allowed-attr).
      sx={{
        position: "relative",
        width: "100%",
        minWidth: 0,
        cursor: enableSorting ? "pointer" : "default",
        justifyContent: right ? "flex-end" : "flex-start",
        textAlign: right ? "right" : "left",
      }}
    >
      <Stack
        sx={{ minWidth: 0 }}
        alignItems={right ? "flex-end" : "flex-start"}
      >
        <Typography
          component="span"
          typography="s2"
          fontWeight="fontWeightSemiBold"
          color="text.primary"
          sx={{ lineHeight: 1.3 }}
        >
          {label}
        </Typography>
        {agg && (
          <Typography
            component="span"
            typography="s3"
            color="text.disabled"
            sx={{ lineHeight: 1.2 }}
          >
            {agg}
          </Typography>
        )}
      </Stack>
      {sort && (
        <Iconify
          icon={sort === "asc" ? "mdi:arrow-up" : "mdi:arrow-down"}
          width={14}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      )}
      {showColumnMenu && (
        // Over the cell's right padding: takes no room from the label.
        <Box
          sx={{
            position: "absolute",
            right: -16,
            top: "50%",
            transform: "translateY(-50%)",
            display: "inline-flex",
          }}
        >
          <HeaderMenuButton
            label={displayName}
            onOpen={(el) => showColumnMenu(el)}
            sx={{
              ml: 0,
              bgcolor: "background.paper",
              "&:hover": { bgcolor: "action.hover" },
            }}
          />
        </Box>
      )}
    </Stack>
  );
}
HeaderCell.propTypes = {
  displayName: PropTypes.string,
  column: PropTypes.object,
  progressSort: PropTypes.func,
  enableSorting: PropTypes.bool,
  term: PropTypes.string,
  agg: PropTypes.string,
  tip: PropTypes.string,
  showColumnMenu: PropTypes.func,
};

/**
 * W1-6: the run's one ledger under its name, each number a
 * link to its rows on the results page (an errored number opens the
 * Errored filter of this run, by cause when it has one).
 */
function RunLedgerLinks({ run, links, noun }) {
  const parts = runLedgerParts(run, { noun });
  if (!parts.length) return null;
  const hrefOf = (part) => {
    if (!links?.pill) return null;
    if (part.key === "errored")
      return links.pill({
        type: "error",
        run: run.run_id,
        ...(part.cause ? { cause: part.cause } : {}),
      });
    return part.ids?.length
      ? links.pill(
          rowsPill(part.ids, { label: `${run.label} · ${part.label}` }),
        )
      : null;
  };
  return (
    <Typography
      component="div"
      typography="s3"
      color="text.secondary"
      noWrap
      data-testid="summary-run-ledger"
      title={parts.map((p) => p.text).join(" · ")}
      sx={{ minWidth: 0, lineHeight: 1.2 }}
    >
      {parts.map((part, i) => {
        const href = hrefOf(part);
        return (
          <React.Fragment key={part.key}>
            {i > 0 ? " · " : ""}
            {href ? (
              <Link
                component={RouterLink}
                to={href}
                underline="hover"
                color="inherit"
                onClick={(e) => e.stopPropagation()}
              >
                {part.text}
              </Link>
            ) : (
              part.text
            )}
          </React.Fragment>
        );
      })}
    </Typography>
  );
}
RunLedgerLinks.propTypes = {
  run: PropTypes.object.isRequired,
  links: PropTypes.object,
  noun: PropTypes.string,
};

function RunCell({ data, colDef }) {
  const { colors, links, noun } = colDef.cellRendererParams;
  const run = data;
  const status = getRunStatus(run);
  const full = [
    `${run.label} ${run.name ?? ""}`.trim(),
    run.target_type ? `Kind: ${run.target_type}` : null,
    run.dataset_version ? `Dataset version v${run.dataset_version}` : null,
    `Run id ${run.run_id}`,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <Box
      sx={{
        ...cellBox,
        "& .run-copy": { opacity: 0 },
        "&:hover .run-copy, &:focus-within .run-copy": { opacity: 1 },
      }}
    >
      <Box
        sx={{
          width: 3,
          height: 18,
          borderRadius: 1,
          bgcolor: colors?.[run.run_id]?.solid,
          flexShrink: 0,
        }}
      />
      <Stack sx={{ minWidth: 0 }}>
        <CustomTooltip
          show
          size="small"
          placement="top"
          title={<Box sx={{ whiteSpace: "pre-line" }}>{full}</Box>}
        >
          <Stack
            direction="row"
            gap={0.75}
            alignItems="center"
            sx={{ minWidth: 0 }}
          >
            <Typography
              typography="s2"
              fontWeight={600}
              color="text.primary"
              sx={{ flexShrink: 0 }}
            >
              {run.label}
            </Typography>
            <Typography
              typography="s2"
              color="text.primary"
              noWrap
              sx={{ minWidth: 0, opacity: 0.8 }}
            >
              {run.name}
            </Typography>
          </Stack>
        </CustomTooltip>
        <RunLedgerLinks run={run} links={links} noun={noun} />
      </Stack>
      {run.is_baseline && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          title={
            run.is_pinned_baseline
              ? "Baseline of this view, and the dataset's pinned baseline"
              : "Baseline of this view (not pinned for the dataset)"
          }
        >
          <Typography
            component="span"
            typography="s3"
            color="text.secondary"
            sx={{
              flexShrink: 0,
              display: "inline-flex",
              alignItems: "center",
              gap: 0.25,
              px: 0.5,
              borderRadius: 0.5,
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            {run.is_pinned_baseline && (
              <Iconify icon="mdi:pin-outline" width={12} />
            )}
            Baseline
          </Typography>
        </CustomTooltip>
      )}
      {!status.terminal && <RunStatusChip run={run} showProgress compact />}
      <Box className="run-copy" sx={{ ml: "auto", flexShrink: 0 }}>
        <CopyButton
          value={run.run_id}
          tooltip={`Copy run id ${run.run_id}`}
          iconSize={14}
        />
      </Box>
    </Box>
  );
}
RunCell.propTypes = { data: PropTypes.object, colDef: PropTypes.object };

/**
 * Value with its `▲ +12.3 pts` delta vs the baseline right under it (each
 * with a tooltip), so the default columns fit 1280 px; the 95% CI is a
 * third line when switched on.
 */
function StatCell({ data, colDef }) {
  const { get, base, baseLabel, delta, ci } = colDef.cellRendererParams;
  const { text, tip, href } = get(data);
  const d = base && data.run_id !== base.run_id ? delta?.(data, base) : null;
  const ciText = ci?.(data);
  const value = (
    <Typography
      component="span"
      typography="s2"
      fontWeight={600}
      color={text === EMPTY ? "text.disabled" : "text.primary"}
      sx={NUM_SX}
    >
      {text}
    </Typography>
  );
  return (
    <Stack
      alignItems="flex-end"
      justifyContent="center"
      sx={{ height: "100%", minWidth: 0, lineHeight: 1.25 }}
    >
      {tip ? (
        <CustomTooltip show size="small" placement="top" title={tip}>
          {href ? (
            <Link component={RouterLink} to={href} underline="hover">
              {value}
            </Link>
          ) : (
            value
          )}
        </CustomTooltip>
      ) : (
        value
      )}
      {d && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          title={
            d.tip
              ? `${d.text} vs ${baseLabel}: ${d.tip}`
              : `${d.text} vs ${baseLabel} (${get(base).text} → ${text})`
          }
        >
          <Typography
            component="span"
            typography="s3"
            fontWeight="fontWeightMedium"
            aria-label={`${d.dir === "up" ? "up" : d.dir === "down" ? "down" : "no change"} ${d.text} vs ${baseLabel}`}
            sx={{ ...NUM_SX, color: TONE_FG[d.tone], whiteSpace: "nowrap" }}
          >
            {d.glyph ? `${d.glyph} ` : ""}
            {d.text}
          </Typography>
        </CustomTooltip>
      )}
      {base && data.run_id === base.run_id && (
        <Typography component="span" typography="s3" color="text.disabled">
          baseline
        </Typography>
      )}
      {ciText && (
        <Typography typography="s3" color="text.disabled" sx={NUM_SX}>
          95% CI {ciText}
        </Typography>
      )}
    </Stack>
  );
}
StatCell.propTypes = { data: PropTypes.object, colDef: PropTypes.object };

function MetricValueCell({ data, colDef }) {
  const { metric, baselineId, baselineLabel } = colDef.cellRendererParams;
  const stats = metric.per_run.find((p) => p.run_id === data.run_id);
  const vs = metric.vs_baseline.find((v) => v.run_id === data.run_id);
  // L7c g6 (sum:all-runs): one delta for every surface — R5's paired
  // `vs_baseline.delta` (the verdict, Why card, print, CSV and Falcon read
  // it), rounded once by the badge. A run with errored rows no longer
  // shows a second, unpaired number here.
  const delta = vs?.delta;
  return (
    <Stack
      alignItems="flex-end"
      justifyContent="center"
      sx={{ height: "100%", minWidth: 0, lineHeight: 1.25 }}
    >
      <Typography
        typography="s2"
        fontWeight={600}
        color={stats?.partial ? "text.disabled" : "text.primary"}
        sx={NUM_SX}
      >
        {stats?.n
          ? formatValue(metric, stats.value, { agg: aggOf(metric) })
          : EMPTY}
      </Typography>
      {vs && data.run_id !== baselineId && (
        <DeltaBadge
          def={metric}
          delta={delta}
          ci95={vs.ci95}
          significant={vs.significant}
          n={vs.n_shared}
          vsLabel={baselineLabel}
          typography="s3"
        />
      )}
      {data.run_id === baselineId && (
        <Typography component="span" typography="s3" color="text.disabled">
          baseline
        </Typography>
      )}
    </Stack>
  );
}
MetricValueCell.propTypes = {
  data: PropTypes.object,
  colDef: PropTypes.object,
};

function LinkCountCell({ data, colDef }) {
  const { href, text, tone, tip } = colDef.cellRendererParams.render(data);
  const body = href ? (
    <Link
      component={RouterLink}
      to={href}
      underline="hover"
      color={tone ?? "primary.main"}
      typography="s2"
      sx={{ fontWeight: 600, ...NUM_SX }}
    >
      {text}
    </Link>
  ) : (
    <Typography
      typography="s2"
      color={text === EMPTY ? "text.disabled" : "text.secondary"}
      sx={NUM_SX}
    >
      {text}
    </Typography>
  );
  return (
    <Box sx={{ ...cellBox, justifyContent: "flex-end" }}>
      {tip ? (
        <CustomTooltip show size="small" placement="top" title={tip}>
          <span>{body}</span>
        </CustomTooltip>
      ) : (
        body
      )}
    </Box>
  );
}
LinkCountCell.propTypes = { data: PropTypes.object, colDef: PropTypes.object };

const header = (key, term) => ({
  headerComponent: HeaderCell,
  headerComponentParams: {
    term,
    agg: HEADER_HELP[key]?.agg,
    tip: HEADER_HELP[key]?.tip,
  },
});

/**
 * All runs (SVP-4.6, one view A5-06…A5-10): one row per run. Columns are
 * the user's (Columns + `N hidden`, header right-click / ⋮ / Shift+F10:
 * sort, freeze, move, hide; remembered per dataset): by default Run ·
 * Success rate · Cost per task · Time per task · Passes every eval · Errors
 * · the first 2 evals, the rest one click away. `Table | Radar` (R) and the
 * 95% CI switch (remembered) always. Δ vs the baseline under every value,
 * glyph + colour; frozen Run column with a scroll shadow.
 */
const AllRunsTable = ({
  analysis,
  colors,
  ranks,
  links,
  chart = "table",
  onChart,
}) => {
  const view = chart;
  const tableRef = useRef(null);
  const gridRef = useRef(null);
  const metrics = analysis.metrics;
  const evals = useMemo(
    () => metrics.filter((m) => m.family === "eval"),
    [metrics],
  );
  const baselineId = analysis.baseline_run_id;
  const baselineLabel = analysis.baseline_label;
  const base = analysis.runs.find((r) => r.run_id === baselineId) ?? null;
  const hasRank = Object.values(ranks ?? {}).some(Boolean);
  const maxTrials = Math.max(1, ...analysis.runs.map((r) => r.trials ?? 1));

  // The user's columns (per dataset: the evals are the dataset's) and the
  // remembered 95% CI switch (a personal display choice, not in the URL).
  const scopeId = analysis.dataset_id ?? undefined;
  const catalog = useMemo(
    () => allRunsCatalog({ evals, maxTrials, hasRank }),
    [evals, maxTrials, hasRank],
  );
  const layout = useColumnLayout({
    surface: "allRuns",
    scopeId,
    columns: catalog,
    commandLabel: "Columns of All runs…",
  });
  const shaping = useAgColumnShaping({ gridRef, layout });
  const [prefs, patchPrefs] = useLayoutPrefs("allRuns", scopeId);
  const ci = Boolean(prefs.extras?.ci95);
  const setCi = (on) => patchPrefs({ extras: { ci95: on || undefined } });
  const edges = useScrollEdges(tableRef, ".ag-center-cols-viewport", [
    view,
    layout.visibleIds.join(","),
  ]);

  const rows = useMemo(
    () =>
      analysis.runs.map((r) => ({
        ...r,
        // L7c g6 (sum:baseline): None mode (no baseline in view) tags no run.
        is_baseline: Boolean(baselineId) && r.run_id === baselineId,
        rank: ranks?.[r.run_id] ?? null,
      })),
    [analysis.runs, ranks, baselineId],
  );

  // Few columns shown: the Run column may take more room for long names.
  const runMinWidth = layout.visibleIds.length <= 5 ? 300 : 200;
  // The layout owns hide / pin (applied on grid ready); `initialHide` /
  // `initialPinned` only shape the very first paint, so the grid never
  // flashes every column before the user's layout lands.
  const firstLayout = useRef(null);
  if (!firstLayout.current)
    firstLayout.current = {
      visible: new Set(layout.visibleIds),
      pinned: new Set(
        layout.columns.filter((c) => c.pinned === "left").map((c) => c.id),
      ),
    };

  const rowNoun = analysis?.noun === "task" ? "task" : "row";
  const columns = useMemo(() => {
    const cols = [];
    const num = {
      type: "numericColumn",
      wrapHeaderText: true,
      autoHeaderHeight: true,
    };
    if (hasRank)
      cols.push({
        colId: "rank",
        field: "rank",
        headerName: "Ranking",
        width: 110,
        cellRenderer: RankWithIndexRenderer,
        headerComponent: HeaderCell,
      });
    cols.push({
      colId: "run",
      field: "label",
      headerName: "Run",
      minWidth: runMinWidth,
      flex: 2.5,
      suppressMovable: true,
      lockPosition: "left",
      cellRenderer: RunCell,
      cellRendererParams: { colors, links, noun: rowNoun },
      headerComponent: HeaderCell,
      comparator: (a, b, na, nb) =>
        na.data.experiment_ordinal - nb.data.experiment_ordinal ||
        String(na.data.letter).localeCompare(nb.data.letter),
    });
    const stat = (colId, name, key, term, params, extra = {}) => ({
      ...num,
      ...header(key, term),
      colId,
      headerName: name,
      minWidth: 96,
      flex: 1,
      cellRenderer: StatCell,
      cellRendererParams: { base, baseLabel: baselineLabel, ...params },
      ...extra,
    });
    cols.push(
      stat(
        "success_rate",
        "Success rate",
        "success",
        "successRate",
        {
          get: successText,
          // IT-R2 G2 P3: when one run skipped tasks, the delta is over the
          // tasks BOTH ran (the verdict's +24.8 pts, never a second +25.9).
          delta: (d, b) => {
            const t = d.tasks_vs_baseline;
            const scoped =
              t &&
              t.not_run > 0 &&
              Number.isFinite(t.cand_rate) &&
              Number.isFinite(t.base_rate);
            const info = scoped
              ? deltaInfo("pct", t.cand_rate, t.base_rate)
              : deltaInfo("pct", d.success_rate, b.success_rate);
            return scoped && info
              ? {
                  ...info,
                  tip: `${info.text} on the ${t.both} ${analysis?.noun ?? "task"}s both ran (${fmtPct(t.base_rate)} → ${fmtPct(t.cand_rate)}); ${t.not_run} only one run ran`,
                }
              : info;
          },
          ci: ci ? successCi : undefined,
        },
        { valueGetter: (p) => p.data?.success_rate ?? null, minWidth: 104 },
      ),
    );
    if (maxTrials > 1)
      cols.push(
        stat(
          "reliable_rate",
          `Reliable on all ${maxTrials} tries`,
          "reliable",
          "reliable",
          {
            get: reliableText,
            delta: (d, b) => deltaInfo("pct", d.reliable_rate, b.reliable_rate),
          },
          { valueGetter: (p) => p.data?.reliable_rate ?? null, minWidth: 120 },
        ),
      );
    cols.push(
      stat(
        "cost_per_row",
        "Cost per task",
        "cost",
        "costPerTask",
        {
          get: costText,
          delta: (d, b) =>
            deltaInfo("cost", d.cost_per_row, b.cost_per_row, {
              higherIsBetter: false,
            }),
        },
        { valueGetter: (p) => p.data?.cost_per_row ?? null },
      ),
    );
    cols.push(
      stat(
        "average_response_time",
        "Time per task",
        "time",
        "timePerTask",
        {
          get: timeText,
          delta: (d, b) =>
            deltaInfo("ms", d.latency_p50_ms, b.latency_p50_ms, {
              higherIsBetter: false,
            }),
        },
        { valueGetter: (p) => p.data?.latency_p50_ms ?? null },
      ),
    );
    cols.push(
      stat(
        "pass_rate",
        "Passes every eval",
        "passesEveryEval",
        undefined,
        {
          get: passesEveryEvalText,
          delta: (d, b) => deltaInfo("pct", d.pass_rate, b.pass_rate),
        },
        { valueGetter: (p) => p.data?.pass_rate ?? null, minWidth: 108 },
      ),
    );
    if (maxTrials > 1)
      cols.push({
        ...num,
        ...header("trials", "flaky"),
        colId: "flaky_rows",
        field: "flaky_rows",
        headerName: "Trials",
        minWidth: 120,
        flex: 1,
        cellRenderer: LinkCountCell,
        cellRendererParams: {
          render: (d) =>
            d.trials > 1
              ? {
                  text: `×${d.trials} · ${d.flaky_rows} flaky`,
                  href: d.flaky_rows ? links.flaky(d.run_id) : null,
                  tone: "warning.dark",
                  tip: d.flaky_rows
                    ? `${d.flaky_rows} tasks passed some trials and failed others. Click to see them.`
                    : "No flaky tasks: every task gave the same result on every try.",
                }
              : {
                  text: "×1",
                  tip: "One try per task: flakiness can't be measured.",
                },
        },
      });
    cols.push({
      ...num,
      ...header("errors"),
      colId: "errored_rows",
      field: "errored_rows",
      headerName: "Errors",
      minWidth: 84,
      flex: 0.6,
      cellRenderer: LinkCountCell,
      cellRendererParams: {
        render: (d) => ({
          text: String(d.errored_rows ?? 0),
          href: d.errored_rows ? links.errors(d.run_id) : null,
          tone: "error.main",
          tip: d.errored_rows
            ? `${d.errored_rows} rows errored. Click to open them.`
            : "No errored rows.",
        }),
      },
    });
    evals.forEach((m) =>
      cols.push({
        ...num,
        headerComponent: HeaderCell,
        headerComponentParams: {
          agg: aggOf(m) === "pass_rate" ? "pass rate" : aggOf(m) ?? "mean",
          tip: `${m.name}: ${m.description ?? "eval score"}. ${m.higher_is_better === false ? "Lower" : "Higher"} is better.${m.versions ? ` Versions ${Object.values(m.versions).filter(Boolean).join(", ")}.` : ""}`,
        },
        colId: m.key,
        headerName: m.name,
        minWidth: 132,
        flex: 1,
        valueGetter: (p) =>
          m.per_run.find((x) => x.run_id === p.data?.run_id)?.value ?? null,
        cellRenderer: MetricValueCell,
        cellRendererParams: { metric: m, baselineId, baselineLabel },
        cellStyle: { padding: "0 12px" },
      }),
    );
    cols.push({
      ...num,
      ...header("p99"),
      colId: "latency_p99",
      headerName: "Time p99",
      minWidth: 96,
      flex: 1,
      valueGetter: (p) => p.data?.latency_p99_ms ?? null,
      cellRenderer: LinkCountCell,
      cellRendererParams: {
        render: (d) => ({
          text: d.latency_p99_ms == null ? EMPTY : fmtMs(d.latency_p99_ms),
        }),
      },
    });
    cols.push({
      ...num,
      ...header("tokens"),
      colId: "total_token",
      field: "avg_tokens",
      headerName: "Tokens per task",
      minWidth: 112,
      flex: 1,
      cellRenderer: LinkCountCell,
      cellRendererParams: { render: (d) => tokensText(d) },
    });
    cols.push({
      ...num,
      headerComponent: HeaderCell,
      headerComponentParams: {
        term: "totalCost",
        agg: "sum",
        tip: "Everything this run spent: its tries and its evals' judge calls (hover a cell for the split and the estimate).",
      },
      colId: "cost_total",
      field: "cost_total",
      headerName: "Total cost",
      minWidth: 96,
      flex: 1,
      // W1-6: one number, the run's actual spend (R1 `actual`:
      // tries + judge calls), the same one the hover splits.
      valueGetter: (p) => spentOf(p.data),
      cellRenderer: LinkCountCell,
      cellRendererParams: {
        render: (d) => ({
          text: fmtCost(spentOf(d)),
          tip: spendTip(d) ?? undefined,
        }),
      },
    });
    const first = firstLayout.current;
    return cols.map((c) => ({
      ...c,
      initialHide: !first.visible.has(c.colId),
      initialPinned: first.pinned.has(c.colId) ? "left" : undefined,
    }));
  }, [
    evals,
    colors,
    base,
    baselineId,
    baselineLabel,
    hasRank,
    maxTrials,
    links,
    ci,
    runMinWidth,
    rowNoun,
  ]);

  // L7c g6: the chart is a radar from 4 evals, grouped bars below
  // (SpiderChartExperiment), and the toggle names what it shows.
  const chartWord = allRunsChartWord(evals.length);
  // Radar / grouped bars: every eval in 0–1, run colours, the table's format.
  const radar = useMemo(
    () => ({
      series: analysis.runs.map((r) => ({
        id: r.run_id,
        label: r.label,
        name: r.name,
        color: colors?.[r.run_id]?.solid,
        values: Object.fromEntries(
          evals.map((m) => [
            m.key,
            m.per_run.find((p) => p.run_id === r.run_id)?.value ?? null,
          ]),
        ),
        n: Object.fromEntries(
          evals.map((m) => [
            m.key,
            m.per_run.find((p) => p.run_id === r.run_id)?.n ?? null,
          ]),
        ),
      })),
      metrics: evals.map((m) => ({
        key: m.key,
        name: m.name,
        fmt: (v) => formatValue(m, v, { agg: aggOf(m) }),
        fmtDelta: (v, b) => formatDelta(m, v - b, { agg: aggOf(m) }),
      })),
    }),
    [analysis.runs, evals, colors],
  );

  return (
    <AnalysisCard
      id="analysis-all-runs"
      icon="mdi:format-list-bulleted"
      title="All runs"
      titleAdornment={
        <Typography typography="s2" color="text.secondary" noWrap>
          {analysis.runs.length} runs · {analysis.examples_in_view} tasks in
          view
        </Typography>
      }
      actions={
        <Stack direction="row" alignItems="center" gap={1}>
          {view === "table" && (
            <ColumnsMenu layout={layout} buttonVariant="label" />
          )}
          <CustomTooltip
            show={view !== "table"}
            size="small"
            placement="top"
            title="The 95% CI shows in the table"
          >
            <Box component="span" sx={{ display: "inline-flex" }}>
              <FormControlLabel
                disabled={view !== "table"}
                control={
                  <Switch
                    size="small"
                    checked={ci}
                    onChange={(e) => setCi(e.target.checked)}
                  />
                }
                label={
                  <Typography typography="s2" color="text.secondary">
                    <Term k="ci95">95% CI</Term>
                  </Typography>
                }
                sx={{ mr: 0 }}
              />
            </Box>
          </CustomTooltip>
          {/* IT-R5 G6 P3: one toggle (was `Table | Radar`, two controls):
              it names the view it switches to; R still toggles. */}
          <KeyHintTooltip
            title={
              evals.length === 0 && view === "table"
                ? "The chart needs at least one eval"
                : view === "table"
                  ? chartWord === "Radar"
                    ? "Show as a radar"
                    : "Show as grouped bars (a radar needs 4 or more evals)"
                  : "Show as a table"
            }
            action="toggleChart"
          >
            <Box component="span" sx={{ display: "inline-flex" }}>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                data-testid="all-runs-view-toggle"
                aria-pressed={view === "radar"}
                disabled={view === "table" && evals.length === 0}
                startIcon={
                  <Iconify
                    icon={
                      view === "table"
                        ? chartWord === "Radar"
                          ? "mdi:radar"
                          : "mdi:chart-bar"
                        : "mdi:table"
                    }
                    width={16}
                  />
                }
                onClick={() => onChart?.(view === "table" ? "radar" : "table")}
                sx={{ height: 30, typography: "s2", borderColor: "divider" }}
              >
                {view === "table" ? chartWord : "Table"}
              </Button>
            </Box>
          </KeyHintTooltip>
        </Stack>
      }
      dense
    >
      {view === "table" ? (
        <Box
          ref={tableRef}
          data-qa-clip
          data-more-right={edges.right ? "1" : undefined}
          sx={{
            position: "relative",
            // More columns to the right: a fade on the clipped edge (above
            // the horizontal scrollbar) says "scroll for more".
            "&::after": {
              content: '""',
              position: "absolute",
              top: 0,
              right: 0,
              bottom: 14,
              width: 40,
              pointerEvents: "none",
              zIndex: 2,
              opacity: edges.right ? 1 : 0,
              transition: "opacity 150ms",
              background: (t) =>
                `linear-gradient(to right, ${alpha(t.palette.background.paper, 0)}, ${t.palette.background.paper})`,
            },
            // Sticky Run column: a soft shadow on its right edge so the
            // scrolled metric columns read as sliding under it.
            "& .ag-pinned-left-cols-container, & .ag-pinned-left-header": {
              boxShadow: (t) =>
                `6px 0 6px -6px ${t.palette.mode === "dark" ? "rgba(0,0,0,0.7)" : "rgba(0,0,0,0.25)"}`,
              zIndex: 1,
            },
            "& .ag-cell": { color: "text.primary" },
          }}
        >
          <ExperimentSummaryTable
            columns={columns}
            rows={rows}
            getRowId={(p) => p.data.run_id}
            gridRef={gridRef}
            gridProps={{ ...shaping, rowHeight: ci ? 60 : 48 }}
          />
        </Box>
      ) : (
        <Stack sx={{ p: 1.5 }}>
          <SpiderChartExperiment
            series={radar.series}
            metrics={radar.metrics}
            baselineId={baselineId}
          />
        </Stack>
      )}
    </AnalysisCard>
  );
};

AllRunsTable.propTypes = {
  analysis: PropTypes.object.isRequired,
  colors: PropTypes.object,
  ranks: PropTypes.object,
  links: PropTypes.object.isRequired,
  chart: PropTypes.oneOf(["table", "radar"]),
  onChart: PropTypes.func,
};

export default AllRunsTable;
