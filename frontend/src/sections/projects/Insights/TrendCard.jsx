import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Paper, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { OTHER_ID, buildAreaSeries } from "./insightsDistribution";
import { formatCount, formatPercent, formatWhen } from "./insightsFormat";
import { nearestIndex, tickLabels, trendGeometry } from "./insightsTrend";
import { StackedBar, Swatch } from "./parts";

const CHART_HEIGHT = 176;
const DAY_MS = 24 * 3600 * 1000;
const TOOLTIP_WIDTH = 260;

// Width of an element, kept current as it resizes.
function useWidth(ref) {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () =>
      setWidth(element.getBoundingClientRect().width || 640);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

function TrendTooltip({ row, series, afresh }) {
  const total = Object.values(row.counts).reduce((a, b) => a + b, 0) || 1;
  const items = [...series].reverse().filter((s) => row.counts?.[s.id] > 0);
  return (
    <Paper
      elevation={0}
      sx={{
        p: 1.25,
        width: TOOLTIP_WIDTH,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        boxShadow: (theme) => theme.customShadows?.dropdown,
        pointerEvents: "none",
      }}
    >
      <Typography variant="caption" fontWeight={600} component="div">
        {formatWhen(row.time)}
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        component="div"
        sx={{ mb: 0.75 }}
      >
        {formatCount(row.n)} analysed · click to open this run
      </Typography>
      {afresh && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="div"
          sx={{ mb: 0.75 }}
        >
          Found its categories afresh: its shares don&apos;t compare with
          earlier runs.
        </Typography>
      )}
      <Stack spacing={0.5}>
        {items.map((s) => (
          <Stack key={s.id} direction="row" spacing={0.75} alignItems="center">
            <Swatch color={s.color} size={8} />
            <Typography variant="caption" noWrap sx={{ flex: 1, minWidth: 0 }}>
              {s.name}
            </Typography>
            <Typography
              variant="caption"
              sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
            >
              {formatPercent(row.counts[s.id] / total)}
              <Box
                component="span"
                sx={{
                  color: "text.secondary",
                  ml: 0.75,
                  display: "inline-block",
                  minWidth: 20,
                  textAlign: "right",
                }}
              >
                {formatCount(row.counts[s.id])}
              </Box>
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Paper>
  );
}

TrendTooltip.propTypes = {
  row: PropTypes.object.isRequired,
  series: PropTypes.array.isRequired,
  afresh: PropTypes.bool,
};

/** The 100% stacked area, with a crosshair and tooltip on hover. */
function TrendChart({ rows, series, runId, onSelectRun }) {
  const theme = useTheme();
  const box = useRef(null);
  const width = useWidth(box);
  const [hover, setHover] = useState(-1);
  const geometry = useMemo(
    () =>
      width
        ? trendGeometry({ rows, series, width, height: CHART_HEIGHT })
        : null,
    [rows, series, width],
  );
  const current = rows.findIndex((row) => row.runId === runId);
  const span = rows.length > 1 ? rows[rows.length - 1].time - rows[0].time : 0;
  const tick = (value) =>
    span < 2 * DAY_MS
      ? new Date(value).toLocaleTimeString("en-US", {
          hour: "numeric",
          minute: "2-digit",
        })
      : new Date(value).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
        });
  const ticks = geometry
    ? tickLabels({
        xs: geometry.xs,
        texts: rows.map((row) => tick(row.time)),
        current,
        left: 0,
        right: width,
      })
    : [];
  const pick = (event) => {
    if (!geometry) return -1;
    const rect = event.currentTarget.getBoundingClientRect();
    return nearestIndex(geometry.xs, event.clientX - rect.left);
  };
  const surface = theme.palette.background.paper;
  const axisText = {
    fill: theme.palette.text.secondary,
    fontSize: 11,
    fontFamily: theme.typography.fontFamily,
  };
  const hoverX = geometry && hover >= 0 ? geometry.xs[hover] : null;
  const tipLeft =
    hoverX === null
      ? 0
      : hoverX + 12 + TOOLTIP_WIDTH > width
        ? Math.max(0, hoverX - 12 - TOOLTIP_WIDTH)
        : hoverX + 12;

  return (
    <Box
      ref={box}
      sx={{ position: "relative", height: CHART_HEIGHT }}
      role="img"
      aria-label={`Share of each category in ${rows.length} runs`}
    >
      {geometry && (
        <svg
          width={width}
          height={CHART_HEIGHT}
          style={{ display: "block", cursor: "pointer" }}
          onMouseMove={(event) => setHover(pick(event))}
          onMouseLeave={() => setHover(-1)}
          onClick={(event) => {
            const index = pick(event);
            if (index >= 0 && rows[index].runId !== runId)
              onSelectRun(rows[index].runId);
          }}
        >
          {[0, 0.5, 1].map((share) => (
            <g key={share}>
              <line
                x1={geometry.inner.left}
                x2={geometry.inner.right}
                y1={geometry.yOf(share)}
                y2={geometry.yOf(share)}
                stroke={theme.palette.divider}
                strokeWidth={1}
              />
              <text
                x={geometry.inner.left - 8}
                y={geometry.yOf(share)}
                textAnchor="end"
                dominantBaseline="middle"
                style={axisText}
              >
                {Math.round(share * 100)}%
              </text>
            </g>
          ))}
          {geometry.bands.map((band) => (
            <path
              key={band.id}
              d={band.d}
              fill={band.color}
              fillOpacity={0.92}
              stroke={surface}
              strokeWidth={1.5}
              strokeLinejoin="round"
            />
          ))}
          {geometry.breaks.map((item) => (
            <line
              key={`break-${rows[item.index].runId}`}
              data-break={rows[item.index].runId}
              x1={item.x}
              x2={item.x}
              y1={geometry.inner.top - 4}
              y2={geometry.inner.bottom}
              stroke={theme.palette.text.secondary}
              strokeWidth={1.25}
              strokeDasharray="4 3"
            />
          ))}
          {ticks.map(({ index, anchor }) => (
            <text
              key={rows[index].runId}
              x={geometry.xs[index]}
              y={CHART_HEIGHT - 6}
              textAnchor={anchor}
              style={{
                ...axisText,
                fontWeight: index === current ? 600 : 400,
                fill:
                  index === current
                    ? theme.palette.text.primary
                    : theme.palette.text.secondary,
              }}
            >
              {tick(rows[index].time)}
            </text>
          ))}
          {current >= 0 && (
            <line
              x1={geometry.xs[current]}
              x2={geometry.xs[current]}
              y1={geometry.inner.top - 4}
              y2={geometry.inner.bottom}
              stroke={theme.palette.text.primary}
              strokeWidth={2}
            />
          )}
          {hoverX !== null && hover !== current && (
            <line
              x1={hoverX}
              x2={hoverX}
              y1={geometry.inner.top}
              y2={geometry.inner.bottom}
              stroke={theme.palette.text.secondary}
              strokeWidth={1}
            />
          )}
        </svg>
      )}
      {hover >= 0 && (
        <Box
          sx={{
            position: "absolute",
            top: 0,
            left: tipLeft,
            zIndex: 2,
            pointerEvents: "none",
          }}
        >
          <TrendTooltip
            row={rows[hover]}
            series={series}
            afresh={hover > 0 && Boolean(rows[hover].fresh)}
          />
        </Box>
      )}
    </Box>
  );
}

TrendChart.propTypes = {
  rows: PropTypes.array.isRequired,
  series: PropTypes.array.isRequired,
  runId: PropTypes.string,
  onSelectRun: PropTypes.func.isRequired,
};

function TrendLegend({ items, onCategory }) {
  return (
    <Stack
      direction="row"
      useFlexGap
      flexWrap="wrap"
      columnGap={2}
      rowGap={0.5}
      component="ul"
      sx={{ m: 0, p: 0, listStyle: "none" }}
    >
      {items.map((item) => {
        const clickable =
          onCategory && item.id !== OTHER_ID && !item.muted && !item.absent;
        return (
          <Stack
            key={item.id}
            component="li"
            direction="row"
            spacing={0.75}
            alignItems="center"
            sx={{ minWidth: 0, maxWidth: 300 }}
          >
            <Swatch color={item.color} size={8} />
            <Typography
              variant="caption"
              noWrap
              component={clickable ? "button" : "span"}
              type={clickable ? "button" : undefined}
              onClick={clickable ? () => onCategory(item.id) : undefined}
              sx={{
                minWidth: 0,
                p: 0,
                border: 0,
                bgcolor: "transparent",
                font: "inherit",
                fontSize: 12,
                color: item.absent ? "text.disabled" : "text.primary",
                cursor: clickable ? "pointer" : "default",
                "&:hover": clickable
                  ? { textDecoration: "underline" }
                  : undefined,
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                },
              }}
            >
              {item.name}
            </Typography>
            {item.absent ? (
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ flexShrink: 0 }}
              >
                not in this run
              </Typography>
            ) : (
              item.share !== undefined && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
                >
                  {formatPercent(item.share)}
                </Typography>
              )
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}

TrendLegend.propTypes = {
  items: PropTypes.array.isRequired,
  onCategory: PropTypes.func,
};

function RunPager({ runs, runId, onSelectRun }) {
  const index = runs.findIndex((run) => run.id === runId);
  if (index < 0) return null;
  const older = runs[index + 1];
  const newer = runs[index - 1];
  const current = runs[index];
  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      aria-label="Runs of this report"
      component="nav"
    >
      <Button
        size="small"
        color="inherit"
        disabled={!older}
        onClick={() => onSelectRun(older.id)}
        startIcon={<Iconify icon="mdi:arrow-left" width={16} />}
        sx={{ fontWeight: 500, color: "text.secondary" }}
      >
        Older
      </Button>
      <Typography
        variant="body2"
        sx={{ fontVariantNumeric: "tabular-nums", fontSize: 13 }}
      >
        {formatWhen(current.finished_at || current.created_at)}
        <Box component="span" sx={{ color: "text.secondary" }}>
          {" "}
          · Run {formatCount(runs.length - index)} of {formatCount(runs.length)}
        </Box>
      </Typography>
      <Button
        size="small"
        color="inherit"
        disabled={!newer}
        onClick={() => onSelectRun(newer.id)}
        endIcon={<Iconify icon="mdi:arrow-right" width={16} />}
        sx={{ fontWeight: 500, color: "text.secondary" }}
      >
        Newer
      </Button>
    </Stack>
  );
}

RunPager.propTypes = {
  runs: PropTypes.array.isRequired,
  runId: PropTypes.string,
  onSelectRun: PropTypes.func.isRequired,
};

/**
 * Share of each top-level category across the report's runs: a 100% stacked
 * area over run time, its legend, and the run pager. With one run, a single
 * stacked bar stands in for the chart.
 */
/**
 * Share of each top-level category across the report's runs: a 100% stacked
 * area over run time, its legend, and the run pager. With one run, a single
 * stacked bar stands in for the chart.
 */
export default function TrendCard({
  runs,
  runId,
  order,
  currentSegments,
  sharedTaxonomy,
  onSelectRun,
  onCategory,
  onRunNow,
}) {
  const theme = useTheme();
  const mode = theme.palette.mode;
  const { series, rows } = useMemo(
    () => buildAreaSeries(runs, { order, mode }),
    [runs, order, mode],
  );
  const current = rows.find((row) => row.runId === runId);
  const chart = rows.length >= 2;
  const breaks = rows.filter((row, i) => i > 0 && row.fresh).length;
  // The pager below counts every run: beside it, say that the others
  // stopped, failed or still run, rather than that there is only one.
  const soFar =
    runs.length > 1
      ? `Only one of ${formatCount(runs.length)} runs completed so far.`
      : "One run so far.";

  if (!chart && !currentSegments?.length) return null;

  return (
    <Box
      component="section"
      aria-label="Category share across runs"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 2,
        pt: 1.75,
        pb: 1,
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        justifyContent="space-between"
        spacing={2}
        sx={{ mb: 1.25 }}
      >
        <Typography variant="subtitle2" component="h2">
          {chart ? "Category share across runs" : "Category share"}
        </Typography>
        {chart && (
          <Typography variant="caption" color="text.secondary" noWrap>
            {breaks
              ? `Categories were found afresh at ${breaks === 1 ? "the dashed line" : "each dashed line"}`
              : sharedTaxonomy
                ? "Every run uses the same categories, so shares compare"
                : "Some runs found their own categories"}
          </Typography>
        )}
      </Stack>

      {chart ? (
        <TrendChart
          rows={rows}
          series={series}
          runId={runId}
          onSelectRun={onSelectRun}
        />
      ) : (
        <Stack spacing={1}>
          <StackedBar
            segments={currentSegments}
            height={20}
            onSegmentClick={onCategory}
          />
          <Typography variant="caption" color="text.secondary">
            {soFar}{" "}
            {onRunNow ? (
              <Box
                component="button"
                type="button"
                onClick={onRunNow}
                sx={{
                  p: 0,
                  border: 0,
                  bgcolor: "transparent",
                  font: "inherit",
                  color: "primary.main",
                  cursor: "pointer",
                  "&:hover": { textDecoration: "underline" },
                }}
              >
                Run it again
              </Box>
            ) : (
              "Run it again"
            )}{" "}
            to see how this moves.
          </Typography>
        </Stack>
      )}

      <Box sx={{ mt: 1.25 }}>
        <TrendLegend
          items={
            chart
              ? series
                  .map((s) => {
                    // A category of other runs only (categories found afresh)
                    // is not one this run sorted into: never "0%".
                    const absent =
                      Boolean(current) && !(s.id in current.counts);
                    return {
                      ...s,
                      absent,
                      share:
                        current && !absent
                          ? current.shares[s.id] || 0
                          : undefined,
                    };
                  })
                  // Largest first in the run on screen; Other after them, and
                  // other runs' categories last.
                  .sort(
                    (a, b) =>
                      Number(a.absent) - Number(b.absent) ||
                      Number(Boolean(a.muted)) - Number(Boolean(b.muted)) ||
                      (b.share || 0) - (a.share || 0),
                  )
              : currentSegments
          }
          onCategory={onCategory}
        />
      </Box>

      {runs.length > 1 && (
        <Box
          sx={{
            mt: 1,
            pt: 0.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <RunPager runs={runs} runId={runId} onSelectRun={onSelectRun} />
        </Box>
      )}
    </Box>
  );
}

TrendCard.propTypes = {
  runs: PropTypes.array.isRequired,
  runId: PropTypes.string,
  order: PropTypes.array.isRequired,
  currentSegments: PropTypes.array,
  sharedTaxonomy: PropTypes.bool,
  onSelectRun: PropTypes.func.isRequired,
  onCategory: PropTypes.func,
  onRunNow: PropTypes.func,
};
