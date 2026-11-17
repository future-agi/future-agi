import React, { useCallback, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { formatValue } from "src/sections/common/Metrics/metricFormat";
import {
  fmtCost as fmtCostValue,
  fmtMs as fmtMsValue,
  fmtPct,
} from "src/sections/common/format/fmt";
import { useViewUrlState } from "src/sections/common/ux/viewState";
import ExperimentMetricChart from "./ExperimentMetricChart";
// HO results R7 (W5 B-H2): the dataset's run metrics in the Scores picker.
import { useParams } from "react-router-dom";
import { useDerivedMetrics } from "src/sections/common/DerivedMetrics/useDerivedMetrics";
import {
  isRunMetricKey,
  runMetricAxis,
  runMetricSeries,
  runMetricSummary,
} from "src/sections/common/DerivedMetrics/stripRunMetrics";
import { CHART_CARDS } from "./leaderboardPrefs";
import { STRIP_RUNS, aggValueOf, stripRuns } from "./leaderboardModel";
import {
  CHART_VIEW_DEFAULTS,
  LATENCY_COLORS,
  SERIES_COLORS,
  costOf,
  errorRateOf,
  evalSummary,
  latencyGetters,
  normalizedScore,
  runNotes,
  scoreDefs,
  summarize,
  zoomRange,
} from "./metricStripModel";

const MS = { key: "ops:latency", output_type: "ms", higher_is_better: false };
const USD = { key: "ops:cost", output_type: "usd", higher_is_better: false };
const PCT = (v) => fmtPct(v / 100);
const fmtMs = (s) => fmtMsValue(s * 1000);
const fmtUsd = (v) => formatValue(USD, v);
const fmtCost = (v) => fmtCostValue(v);

const RunRef = ({ run }) => (
  <Box
    component="span"
    sx={{ fontWeight: 600, color: "text.primary", whiteSpace: "nowrap" }}
  >
    {run.label}
  </Box>
);

RunRef.propTypes = { run: PropTypes.object.isRequired };

const Num = ({ children }) => (
  <Box
    component="span"
    sx={{
      color: "text.primary",
      fontVariantNumeric: "tabular-nums",
      whiteSpace: "nowrap",
    }}
  >
    {children}
  </Box>
);

Num.propTypes = { children: PropTypes.node };

/**
 * `Correctness: latest #7B 54.5% vs #1A 79.4% · best #2B 86.6%` (two lines
 * at most). `vs` is the baseline run's value; `best` is skipped when it is
 * the latest run.
 */
const SummaryLine = ({
  prefix,
  latest,
  vs,
  best,
  bestWord = "best",
  format,
}) => (
  <Typography
    typography="s3"
    color="text.secondary"
    component="div"
    sx={{
      display: "-webkit-box",
      WebkitLineClamp: 2,
      WebkitBoxOrient: "vertical",
      overflow: "hidden",
      minHeight: 32,
    }}
  >
    {latest ? (
      <>
        {prefix ? `${prefix}: latest ` : "Latest "}
        <RunRef run={latest.run} /> <Num>{format(latest.value)}</Num>
        {vs && vs.run.run_id !== latest.run.run_id && (
          <>
            {" vs "}
            <RunRef run={vs.run} /> <Num>{format(vs.value)}</Num>
          </>
        )}
        {best && best.run.run_id !== latest.run.run_id && (
          <>
            {` · ${bestWord} `}
            <RunRef run={best.run} /> <Num>{format(best.value)}</Num>
          </>
        )}
        {best &&
          best.run.run_id === latest.run.run_id &&
          ` · also the ${bestWord}`}
      </>
    ) : (
      "No values yet"
    )}
  </Typography>
);

SummaryLine.propTypes = {
  prefix: PropTypes.string,
  latest: PropTypes.object,
  vs: PropTypes.object,
  best: PropTypes.object,
  bestWord: PropTypes.string,
  format: PropTypes.func.isRequired,
};

const Card = ({ title, note, onClose, summary, children }) => (
  <Box
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: 1,
      px: 1.5,
      pt: 1,
      pb: 0.5,
      minWidth: 0,
      height: 188,
      display: "flex",
      flexDirection: "column",
      bgcolor: "background.paper",
    }}
  >
    <Stack direction="row" alignItems="center" justifyContent="space-between">
      <Stack
        direction="row"
        alignItems="baseline"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        {/* EL#25: the title never truncates; the note gives way first. */}
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          noWrap
          sx={{ flexShrink: 0 }}
        >
          {title}
        </Typography>
        {note &&
          (typeof note === "string" ? (
            <CustomTooltip
              show
              title={note}
              size="small"
              placement="top"
              arrow
              enterDelay={400}
            >
              <Typography
                typography="s3"
                color="text.secondary"
                noWrap
                sx={{ minWidth: 0 }}
              >
                {note}
              </Typography>
            </CustomTooltip>
          ) : (
            note
          ))}
      </Stack>
      <CustomTooltip
        show
        title="Hide this chart"
        size="small"
        placement="top"
        arrow
      >
        <IconButton
          size="small"
          onClick={onClose}
          aria-label={`Hide ${title}`}
          sx={{ mr: -0.75 }}
        >
          <Iconify icon="mdi:close" width={14} />
        </IconButton>
      </CustomTooltip>
    </Stack>
    {summary}
    <Box sx={{ flex: 1, minHeight: 0, mt: 0.25 }}>{children}</Box>
  </Box>
);

Card.propTypes = {
  title: PropTypes.string,
  note: PropTypes.node,
  onClose: PropTypes.func,
  summary: PropTypes.node,
  children: PropTypes.node,
};

/**
 * Series legend. With `onSelect` each item is a toggle chip (EL#22/#44):
 * pick one series to auto-zoom it, or `All`. The row scrolls sideways
 * instead of cutting a name.
 */
const Legend = ({ items, selected, onSelect, allLabel = "All" }) => {
  const chip = (key, label, color, active) => {
    const body = (
      <>
        {color && (
          <Box
            sx={{ width: 8, height: 8, borderRadius: "2px", bgcolor: color }}
          />
        )}
        <Typography
          typography="s3"
          color={active ? "text.primary" : "text.secondary"}
          fontWeight={active ? 600 : 400}
          noWrap
        >
          {label}
        </Typography>
      </>
    );
    if (!onSelect)
      return (
        <Stack
          key={key}
          direction="row"
          alignItems="center"
          gap={0.5}
          sx={{ flexShrink: 0 }}
        >
          {body}
        </Stack>
      );
    return (
      <Stack
        key={key}
        component="button"
        type="button"
        role="radio"
        aria-checked={active}
        onClick={() => onSelect(key)}
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{
          flexShrink: 0,
          border: "1px solid",
          borderColor: active ? "primary.main" : "divider",
          bgcolor: active ? "action.selected" : "transparent",
          borderRadius: "10px",
          px: 0.75,
          height: 20,
          cursor: "pointer",
          font: "inherit",
          "&:hover": { bgcolor: "action.hover" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
          },
        }}
      >
        {body}
      </Stack>
    );
  };
  return (
    <Stack
      direction="row"
      gap={onSelect ? 0.5 : 1.25}
      role={onSelect ? "radiogroup" : undefined}
      aria-label={onSelect ? "Series to plot" : undefined}
      sx={{
        minWidth: 0,
        overflowX: "auto",
        scrollbarWidth: "none",
        "&::-webkit-scrollbar": { display: "none" },
      }}
    >
      {onSelect && chip("all", allLabel, null, selected === "all")}
      {items.map((i) =>
        chip(i.key ?? i.name, i.name, i.color, selected === (i.key ?? i.name)),
      )}
    </Stack>
  );
};

Legend.propTypes = {
  items: PropTypes.array,
  selected: PropTypes.string,
  onSelect: PropTypes.func,
  allLabel: PropTypes.string,
};

const LATENCY_MODES = [
  { id: "p50", label: "p50" },
  { id: "p99", label: "p99" },
  { id: "both", label: "Both" },
];

/** EL#23: `p50 | p99 | Both` for the Latency card. */
const LatencyToggle = ({ value, onChange }) => (
  <Stack
    direction="row"
    role="radiogroup"
    aria-label="Latency percentile"
    sx={{
      border: "1px solid",
      borderColor: "divider",
      borderRadius: "4px",
      overflow: "hidden",
      flexShrink: 0,
    }}
  >
    {LATENCY_MODES.map((m) => (
      <Box
        key={m.id}
        component="button"
        type="button"
        role="radio"
        aria-checked={value === m.id}
        onClick={() => onChange(m.id)}
        sx={{
          border: 0,
          font: "inherit",
          typography: "s3",
          px: 0.75,
          height: 18,
          cursor: "pointer",
          color: value === m.id ? "primary.main" : "text.secondary",
          fontWeight: value === m.id ? 600 : 400,
          bgcolor: value === m.id ? "action.selected" : "transparent",
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        {m.label}
      </Box>
    ))}
  </Stack>
);

LatencyToggle.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

const CLICK_HINT = {
  text: "Click to find the row · Shift+click to select",
  muted: true,
};

/**
 * The metric strip above the grid (§3.7): up to four cards over the last 20
 * runs (Scores, Latency p50/p99, Cost, Error rate). Click a point to jump to
 * its row; Shift+click selects it. Hidden under 2 runs.
 */
const ExperimentMetricStrip = ({
  runs,
  metricDefs,
  baselineRun,
  cards,
  onHideCard,
  onPointClick,
}) => {
  const theme = useTheme();
  const mode = theme.palette.mode === "dark" ? "dark" : "light";
  // Narrow cards label every other run so ids never collide.
  const compactLabels = useMediaQuery("(max-width: 1400px)");
  const strip = useMemo(() => stripRuns(runs, STRIP_RUNS), [runs]);
  const categories = useMemo(() => strip.map((r) => r.label), [strip]);
  const baselineIndex = baselineRun
    ? strip.findIndex((r) => r.run_id === baselineRun.run_id)
    : -1;
  const defs = useMemo(() => scoreDefs(metricDefs), [metricDefs]);
  const { dataset: datasetId } = useParams();
  const runMetrics = useDerivedMetrics(
    datasetId,
    strip.map((r) => r.run_id),
  ).data;
  // URL state (F5): which score series and which latency percentile.
  const [chartView, setChartView] = useViewUrlState(CHART_VIEW_DEFAULTS);
  const scoreKey =
    chartView.chart_eval === "all" ||
    defs.some((d) => d.key === chartView.chart_eval) ||
    (isRunMetricKey(chartView.chart_eval) &&
      (runMetrics?.metrics ?? []).some(
        (m) => `dm:${m.id}` === chartView.chart_eval,
      ))
      ? chartView.chart_eval
      : defs[0]?.key ?? "all";
  const latMode = LATENCY_MODES.some((m) => m.id === chartView.chart_lat)
    ? chartView.chart_lat
    : "p50";
  const primary =
    scoreKey === "all" ? defs[0] : defs.find((d) => d.key === scoreKey);

  const click = useCallback(
    (index, event) => strip[index] && onPointClick(strip[index], event),
    [strip, onPointClick],
  );

  const head = useCallback(
    (run) => ({ text: `${run.label} · ${run.name}` }),
    [],
  );

  const allScoreSeries = useMemo(
    () => [
      ...defs.map((d, i) => ({
        key: d.key,
        name: d.higher_is_better === false ? `${d.name} (↓ better)` : d.name,
        color: SERIES_COLORS[mode][i],
        data: strip.map((r) => normalizedScore(r, d)),
      })),
      ...runMetricSeries(
        strip,
        runMetrics,
        SERIES_COLORS[mode].slice(defs.length % SERIES_COLORS[mode].length),
      ),
    ],
    [defs, strip, mode, runMetrics],
  );
  const scoreSeries = useMemo(
    () =>
      scoreKey === "all"
        ? allScoreSeries.filter((x) => !x.runMetric)
        : allScoreSeries.filter((x) => x.key === scoreKey),
    [allScoreSeries, scoreKey],
  );
  const metricAxis = runMetricAxis(scoreSeries[0]);
  const scoreAxis = useMemo(
    () =>
      metricAxis ??
      (scoreKey === "all"
        ? { min: 0, max: 100, zoomed: false }
        : zoomRange(scoreSeries[0]?.data)),
    [scoreKey, scoreSeries, metricAxis],
  );
  const pickedMetric = metricAxis ? scoreSeries[0].runMetric : null;
  const metricLine = pickedMetric
    ? runMetricSummary(strip, runMetrics, pickedMetric, baselineRun)
    : null;

  const scoreTip = useCallback(
    (i) => {
      const run = strip[i];
      if (!run) return [];
      return [
        head(run),
        ...defs.map((d, k) => ({
          color: SERIES_COLORS[mode][k],
          text: `${d.name}: ${formatValue(d, aggValueOf(run, d))}`,
        })),
        ...runNotes(run, primary).map((t) => ({ text: t, muted: true })),
        CLICK_HINT,
      ];
    },
    [strip, defs, mode, primary, head],
  );

  const latencySeries = useMemo(
    () =>
      [
        {
          name: "p50",
          color: LATENCY_COLORS[mode][0],
          data: strip.map((r) => latencyGetters.p50(r)),
        },
        {
          name: "p99",
          color: LATENCY_COLORS[mode][1],
          data: strip.map((r) => latencyGetters.p99(r)),
        },
      ].filter((x) => latMode === "both" || x.name === latMode),
    [strip, mode, latMode],
  );
  const latencyTip = useCallback(
    (i) => {
      const run = strip[i];
      if (!run) return [];
      const p50 = latencyGetters.p50(run);
      const p99 = latencyGetters.p99(run);
      return [
        head(run),
        {
          color: LATENCY_COLORS[mode][0],
          text: `p50 ${p50 == null ? "—" : fmtMs(p50)}`,
        },
        {
          color: LATENCY_COLORS[mode][1],
          text: `p99 ${p99 == null ? "—" : fmtMs(p99)}`,
        },
        ...runNotes(run).map((t) => ({ text: t, muted: true })),
        CLICK_HINT,
      ];
    },
    [strip, mode, head],
  );

  const single = SERIES_COLORS[mode][0];
  const costSeries = useMemo(
    () => [
      { name: "Total cost", color: single, data: strip.map((r) => costOf(r)) },
    ],
    [strip, single],
  );
  const costTip = useCallback(
    (i) => {
      const run = strip[i];
      if (!run) return [];
      const c = costOf(run);
      const done = run.progress?.done;
      return [
        head(run),
        { color: single, text: `Total cost ${c == null ? "—" : fmtUsd(c)}` },
        ...(c != null && done
          ? [{ text: `${fmtUsd(c / done)} per task`, muted: true }]
          : []),
        ...runNotes(run).map((t) => ({ text: t, muted: true })),
        CLICK_HINT,
      ];
    },
    [strip, single, head],
  );

  const errorSeries = useMemo(
    () => [
      {
        name: "Error rate",
        color: single,
        data: strip.map((r) => {
          const v = errorRateOf(r);
          return v == null ? null : Math.round(v * 1000) / 10;
        }),
      },
    ],
    [strip, single],
  );
  const errorTip = useCallback(
    (i) => {
      const run = strip[i];
      if (!run) return [];
      const v = errorRateOf(run);
      return [
        head(run),
        {
          color: single,
          text:
            v == null
              ? "No completed rows"
              : `Error rate ${PCT(v * 100)} · ${run.metrics?.error_count ?? 0} rows`,
        },
        ...runNotes(run).map((t) => ({ text: t, muted: true })),
        CLICK_HINT,
      ];
    },
    [strip, single, head],
  );

  if (strip.length < 2) return null;

  const render = {
    scores: () => {
      const s = primary ? evalSummary(strip, primary) : null;
      return (
        <Card
          key="scores"
          title="Scores"
          note={
            metricAxis
              ? metricAxis.note
              : scoreAxis.zoomed
                ? `axis zoomed ${scoreAxis.min}–${scoreAxis.max}`
                : defs.some((d) => d.higher_is_better === false)
                  ? "0–100 · ↓ better noted"
                  : "0–100"
          }
          onClose={() => onHideCard("scores")}
          summary={
            <>
              {pickedMetric ? (
                <SummaryLine
                  prefix={pickedMetric.name}
                  latest={metricLine?.latest ?? null}
                  vs={metricLine?.vs ?? null}
                  best={metricLine?.best ?? null}
                  format={metricLine?.format ?? ((v) => String(v))}
                />
              ) : (
                <SummaryLine
                  prefix={primary?.name}
                  latest={
                    s && { run: s.latest, value: aggValueOf(s.latest, primary) }
                  }
                  vs={
                    baselineRun &&
                    primary &&
                    aggValueOf(baselineRun, primary) != null
                      ? {
                          run: baselineRun,
                          value: aggValueOf(baselineRun, primary),
                        }
                      : null
                  }
                  best={
                    s && { run: s.best, value: aggValueOf(s.best, primary) }
                  }
                  format={(v) => formatValue(primary, v)}
                />
              )}
              <Legend
                items={allScoreSeries}
                selected={scoreKey}
                onSelect={(key) => setChartView({ chart_eval: key })}
                allLabel="All evals"
              />
            </>
          }
        >
          <ExperimentMetricChart
            type="line"
            categories={categories}
            series={scoreSeries}
            baselineIndex={baselineIndex}
            yMin={scoreAxis.min}
            yMax={scoreAxis.max}
            yFormatter={
              metricAxis?.format ?? ((v) => fmtPct(v / 100, { dp: 0 }))
            }
            tooltipRows={scoreTip}
            onPointClick={click}
            compactLabels={compactLabels}
            height={100}
          />
        </Card>
      );
    },
    latency: () => {
      const latKey = latMode === "p99" ? "p99" : "p50";
      const latGet = latencyGetters[latKey];
      const s = summarize(strip, latGet, { lowerIsBetter: true });
      return (
        <Card
          key="latency"
          title="Latency"
          note={
            <LatencyToggle
              value={latMode}
              onChange={(m) => setChartView({ chart_lat: m })}
            />
          }
          onClose={() => onHideCard("latency")}
          summary={
            <>
              <SummaryLine
                prefix={latKey}
                latest={s && { run: s.latest, value: latGet(s.latest) }}
                vs={
                  baselineRun && latGet(baselineRun) != null
                    ? { run: baselineRun, value: latGet(baselineRun) }
                    : null
                }
                best={s && { run: s.best, value: latGet(s.best) }}
                bestWord="fastest"
                format={fmtMs}
              />
              <Legend items={latencySeries} />
            </>
          }
        >
          <ExperimentMetricChart
            type="bar"
            categories={categories}
            series={latencySeries}
            baselineIndex={baselineIndex}
            yFormatter={(v) => fmtMs(v)}
            tooltipRows={latencyTip}
            onPointClick={click}
            compactLabels={compactLabels}
            height={100}
          />
        </Card>
      );
    },
    cost: () => {
      // $0 means the target reports no token cost (HTTP), not a cheap run.
      const reported = (r) => (costOf(r) > 0 ? costOf(r) : null);
      const s = summarize(strip, reported);
      return (
        <Card
          key="cost"
          title="Total cost"
          note="per run · ↓ better"
          onClose={() => onHideCard("cost")}
          summary={
            <SummaryLine
              latest={s && { run: s.latest, value: costOf(s.latest) }}
              best={s && { run: s.best, value: costOf(s.best) }}
              bestWord="highest"
              format={fmtUsd}
            />
          }
        >
          <ExperimentMetricChart
            type="bar"
            categories={categories}
            series={costSeries}
            baselineIndex={baselineIndex}
            yFormatter={(v) => fmtCost(v)}
            tooltipRows={costTip}
            onPointClick={click}
            compactLabels={compactLabels}
            height={116}
          />
        </Card>
      );
    },
    errors: () => {
      const s = summarize(strip, errorRateOf);
      return (
        <Card
          key="errors"
          title="Error rate"
          note="rows that errored · ↓ better"
          onClose={() => onHideCard("errors")}
          summary={
            <SummaryLine
              latest={s && { run: s.latest, value: errorRateOf(s.latest) }}
              best={
                s && errorRateOf(s.best) > 0
                  ? { run: s.best, value: errorRateOf(s.best) }
                  : null
              }
              bestWord="highest"
              format={(v) => PCT(v * 100)}
            />
          }
        >
          <ExperimentMetricChart
            type="bar"
            categories={categories}
            series={errorSeries}
            baselineIndex={baselineIndex}
            yFormatter={(v) => PCT(v)}
            tooltipRows={errorTip}
            onPointClick={click}
            compactLabels={compactLabels}
            height={116}
          />
        </Card>
      );
    },
  };

  const shown = CHART_CARDS.filter((c) => cards.includes(c.id));
  if (!shown.length) return null;
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "1fr",
          md: `repeat(${Math.min(2, shown.length)}, minmax(0, 1fr))`,
          lg: `repeat(${shown.length}, minmax(0, 1fr))`,
        },
        gap: 1.5,
        px: 1.5,
        pb: 1.5,
      }}
    >
      {shown.map((c) => render[c.id]())}
    </Box>
  );
};

ExperimentMetricStrip.propTypes = {
  runs: PropTypes.array.isRequired,
  metricDefs: PropTypes.array.isRequired,
  baselineRun: PropTypes.object,
  cards: PropTypes.arrayOf(PropTypes.string).isRequired,
  onHideCard: PropTypes.func.isRequired,
  onPointClick: PropTypes.func.isRequired,
};

export default ExperimentMetricStrip;
