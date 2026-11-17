import {
  formatExactMoney,
  sumMoneyValues,
  isMoneyValue,
} from "src/sections/common/format/money";
// Two runs (scatter) (W5-6 B5): x = the baseline's per-row value, y = the
// candidate's, the diagonal y = x. Drag a rectangle to select rows; the
// footer counts them by direction and opens exactly those rows.
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import {
  brushSelect,
  extentOf,
  isDiscrete,
  jitterOf,
  scatterPoints,
  selectionText,
} from "src/sections/common/CompareRuns/scatterModel";
import { fmtValue } from "../workbenchFormat";
import { attemptsOf } from "../workbenchModel";
import { ChartFrame, colColor, useChartTheme } from "./chartKit";

const W = 640;
const H = 340;
const M = { top: 12, right: 16, bottom: 40, left: 56 };
const PW = W - M.left - M.right;
const PH = H - M.top - M.bottom;
const JITTER_SHARE = 0.03;

const TwoRunScatterChart = ({
  matrix,
  measure,
  baseCol,
  candCol,
  candidates,
  onCand,
  colColors,
  onOpenRows,
}) => {
  const t = useChartTheme();
  const svgRef = useRef(null);
  const [drag, setDrag] = useState(null);
  const [rect, setRect] = useState(null);
  const [activePointId, setActivePointId] = useState(null);
  // Coordinates are numeric; exact detail retains the original API decimal strings.
  const plotMatrix = useMemo(
    () =>
      measure.key !== "cost"
        ? matrix
        : {
            ...matrix,
            rows: (matrix?.rows ?? []).map((row) => ({
              ...row,
              cells: Object.fromEntries(
                Object.entries(row.cells ?? {}).map(([key, cell]) => [
                  key,
                  {
                    ...cell,
                    cost_usd: cell.cost_usd?.map((value) =>
                      isMoneyValue(value) ? Number(value) : null,
                    ),
                  },
                ]),
              ),
            })),
          },
    [matrix, measure.key],
  );
  const points = useMemo(
    () =>
      scatterPoints(plotMatrix, {
        baselineId: baseCol?.runIds?.[0],
        candidateId: candCol?.runIds?.[0],
        measure,
      }),
    [plotMatrix, baseCol, candCol, measure],
  );
  const ext = useMemo(() => extentOf(points, measure), [points, measure]);
  const span = ext.max - ext.min;
  const jitter = isDiscrete(points) ? JITTER_SHARE : 0;
  const sx = (v) => M.left + ((v - ext.min) / span) * PW;
  const sy = (v) => M.top + PH - ((v - ext.min) / span) * PH;
  const ix = (px) => ext.min + ((px - M.left) / PW) * span;
  const iy = (py) => ext.min + ((M.top + PH - py) / PH) * span;
  const sel = rect ? brushSelect(points, rect, measure) : null;
  const chosen = new Set(sel?.ids ?? []);

  // Screen → viewBox units (the SVG keeps its aspect ratio).
  const local = (e) => {
    const svg = svgRef.current;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return { x: p.x, y: p.y };
  };
  const onDown = (e) => {
    const p = local(e);
    setDrag({ a: p, b: p });
    setRect(null);
  };
  const onMove = (e) => drag && setDrag({ ...drag, b: local(e) });
  const onUp = () => {
    if (!drag) return;
    const { a, b } = drag;
    setDrag(null);
    if (Math.abs(a.x - b.x) < 3 && Math.abs(a.y - b.y) < 3) return;
    setRect({ x0: ix(a.x), x1: ix(b.x), y0: iy(a.y), y1: iy(b.y) });
  };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ext.min + f * span);
  const fill = colColor(colColors, candCol, t);
  const shown = drag
    ? {
        x: Math.min(drag.a.x, drag.b.x),
        y: Math.min(drag.a.y, drag.b.y),
        w: Math.abs(drag.a.x - drag.b.x),
        h: Math.abs(drag.a.y - drag.b.y),
      }
    : rect
      ? {
          x: sx(Math.min(rect.x0, rect.x1)),
          y: sy(Math.max(rect.y0, rect.y1)),
          w: Math.abs(sx(rect.x1) - sx(rect.x0)),
          h: Math.abs(sy(rect.y1) - sy(rect.y0)),
        }
      : null;

  const pointValue = (p, col, coordinate) => {
    if (measure.key !== "cost")
      return fmtValue(measure, coordinate, { precise: true });
    const row = matrix?.rows?.find((item) => item.row_id === p.id);
    const cell = row?.cells?.[col?.runIds?.[0]];
    // Share the plot producer's eligibility (including billed errors), but
    // retrieve the original decimals rather than its numeric coordinates.
    const plottedCell = plotMatrix?.rows?.find((item) => item.row_id === p.id)
      ?.cells?.[col?.runIds?.[0]];
    const attempts = attemptsOf(plottedCell, measure);
    const values = (cell?.cost_usd ?? []).filter((_, i) => attempts[i] != null);
    if (!values.length) return "—";
    if (values.length === 1) return formatExactMoney(values[0]);
    // An exact ratio avoids pretending a repeating average is a stored decimal.
    return `${formatExactMoney(sumMoneyValues(values))} / ${values.length} (${values.map((value) => formatExactMoney(value)).join(", ")})`;
  };
  const pointDetail = (p) =>
    `${p.label} · ${baseCol?.label} ${pointValue(p, baseCol, p.x)} → ${candCol?.label} ${pointValue(p, candCol, p.y)}`;
  const activePoint = points.find((point) => point.id === activePointId);
  const footer = (
    <Stack spacing={1}>
      <Box
        component="select"
        aria-label="Exact row values"
        value={activePointId ?? ""}
        onChange={(event) => setActivePointId(event.target.value || null)}
        sx={{
          minHeight: 44,
          maxWidth: "100%",
          bgcolor: "background.paper",
          color: "text.primary",
          border: 1,
          borderColor: "divider",
          borderRadius: 1,
          p: 1,
          font: "inherit",
        }}
      >
        <option value="">Choose a row for exact values</option>
        {points.map((point) => (
          <option key={point.id} value={point.id}>
            {point.label}
          </option>
        ))}
      </Box>
      <Typography
        role="status"
        aria-live="polite"
        typography="s2"
        sx={{ overflowWrap: "anywhere" }}
      >
        {activePoint
          ? pointDetail(activePoint)
          : "Focus or tap a point for exact values."}
      </Typography>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        gap={1}
        sx={{ mt: 0.5, minHeight: 30 }}
      >
        <Typography
          typography="s2"
          color={sel ? "text.primary" : "text.secondary"}
          data-testid="scatter-selection"
          data-ids={sel ? sel.ids.join(",") : ""}
        >
          {sel
            ? selectionText(sel)
            : `${points.length} rows · drag a rectangle to select rows`}
        </Typography>
        <Stack direction="row" gap={1}>
          {sel && (
            <Button size="small" onClick={() => setRect(null)}>
              Clear
            </Button>
          )}
          <Button
            size="small"
            variant="outlined"
            disabled={!sel?.ids.length}
            onClick={() => onOpenRows(sel.ids, "Scatter selection")}
          >
            Open these rows
          </Button>
        </Stack>
      </Stack>
    </Stack>
  );

  return (
    <ChartFrame
      title={`Two runs (scatter) · ${measure.label}`}
      legend={
        candidates.length > 1 ? (
          <Select
            size="small"
            value={candCol?.key ?? ""}
            onChange={(e) => onCand(e.target.value)}
            aria-label="Candidate run"
            sx={{ height: 28, typography: "s2" }}
          >
            {candidates.map((c) => (
              <MenuItem key={c.key} value={c.key} sx={{ typography: "s1" }}>
                y = {c.label}
              </MenuItem>
            ))}
          </Select>
        ) : null
      }
      empty={!points.length}
      footer={points.length ? footer : null}
    >
      <Box
        component="svg"
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        data-testid="two-run-scatter"
        data-ext={`${ext.min},${ext.max}`}
        data-plot={`${M.left},${M.top},${PW},${PH}`}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={onUp}
        sx={{ width: "100%", height: "100%", cursor: "crosshair" }}
      >
        {ticks.map((v) => (
          <g key={`t${v}`}>
            <line
              x1={sx(v)}
              x2={sx(v)}
              y1={M.top}
              y2={M.top + PH}
              stroke={t.grid}
            />
            <line
              x1={M.left}
              x2={M.left + PW}
              y1={sy(v)}
              y2={sy(v)}
              stroke={t.grid}
            />
            <text
              x={sx(v)}
              y={M.top + PH + 16}
              fontSize={12}
              fill={t.axis}
              textAnchor="middle"
            >
              {fmtValue(measure, v)}
            </text>
            <text
              x={M.left - 6}
              y={sy(v) + 4}
              fontSize={12}
              fill={t.axis}
              textAnchor="end"
            >
              {fmtValue(measure, v)}
            </text>
          </g>
        ))}
        <line
          x1={sx(ext.min)}
          y1={sy(ext.min)}
          x2={sx(ext.max)}
          y2={sy(ext.max)}
          stroke={t.axis}
          strokeDasharray="4 3"
        />
        <text
          x={M.left + PW / 2}
          y={H - 4}
          fontSize={12}
          fill={t.axis}
          textAnchor="middle"
        >
          {`${baseCol?.label ?? "Baseline"} (baseline) →`}
        </text>
        <text
          x={14}
          y={M.top + PH / 2}
          fontSize={12}
          fill={t.axis}
          textAnchor="middle"
          transform={`rotate(-90 14 ${M.top + PH / 2})`}
        >
          {`${candCol?.label ?? "Candidate"} →`}
        </text>
        {points.map((p) => {
          const j = jitterOf(p.id, span, jitter);
          return (
            <circle
              key={p.id}
              role="button"
              tabIndex={0}
              aria-label={`${p.label} exact values`}
              onFocus={() => setActivePointId(p.id)}
              onMouseDown={(event) => event.stopPropagation()}
              onPointerDown={(event) => {
                event.stopPropagation();
                setActivePointId(p.id);
              }}
              onClick={() => setActivePointId(p.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setActivePointId(p.id);
                }
                if (event.key === "Escape") setActivePointId(null);
              }}
              cx={sx(p.x + j.dx)}
              cy={sy(p.y + j.dy)}
              r={4}
              fill={fill}
              fillOpacity={sel ? (chosen.has(p.id) ? 0.95 : 0.2) : 0.7}
              stroke={t.surface}
              strokeWidth={1}
            >
              <title>{pointDetail(p)}</title>
            </circle>
          );
        })}
        {shown && (
          <rect
            x={shown.x}
            y={shown.y}
            width={shown.w}
            height={shown.h}
            fill={t.primary}
            fillOpacity={0.08}
            stroke={t.primary}
            strokeDasharray="3 2"
          />
        )}
      </Box>
    </ChartFrame>
  );
};

TwoRunScatterChart.propTypes = {
  matrix: PropTypes.object,
  measure: PropTypes.object.isRequired,
  baseCol: PropTypes.object,
  candCol: PropTypes.object,
  candidates: PropTypes.array.isRequired,
  onCand: PropTypes.func.isRequired,
  colColors: PropTypes.object,
  onOpenRows: PropTypes.func.isRequired,
};

export default TwoRunScatterChart;
