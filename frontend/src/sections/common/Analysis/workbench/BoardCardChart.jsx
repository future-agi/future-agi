// One chart card of Explore's board (W5-5 B4). Its numbers come from
// workbenchPivot.buildPivot over its own R3 matrix read (the measure picks
// the matrix values), exactly as the pivot's charts do.
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";
import { useWorkbench } from "src/api/develop/r2";
import { buildPivot, sortedRowKeys } from "./workbenchPivot";
import { matrixMeasureOf } from "./workbenchModel";
import GroupBarsChart from "./charts/GroupBarsChart";
import DistributionChart from "./charts/DistributionChart";
import HeatmapChart from "./charts/HeatmapChart";
import CostQualityChart from "./charts/CostQualityChart";
import TwoRunScatterChart from "./charts/TwoRunScatterChart";
import TrendChart from "./charts/TrendChart";
import DeltaBarsChart from "./charts/DeltaBarsChart";
import { trendPoints } from "./workbenchSeries";
import { fmtValue } from "./workbenchFormat";

const Note = ({ children }) => (
  <Typography typography="s2" color="text.secondary" sx={{ p: 2 }}>
    {children}
  </Typography>
);
Note.propTypes = { children: PropTypes.node };

function BoardCardChart({ card, title, ctx }) {
  const measure =
    ctx.measures.find((m) => m.key === card.measure && !m.disabled) ??
    ctx.measures[0];
  const [cand, setCand] = useState(null);
  const q = useWorkbench(
    {
      datasetId: ctx.datasetId,
      runIds: ctx.runIds,
      baselineId: ctx.baselineId ?? undefined,
      measure: matrixMeasureOf(measure.key),
      filters: ctx.filtersRaw ?? undefined,
    },
    {
      placeholderData: (prev) => prev,
      // Delta bars read the R5 analysis only.
      enabled: card.chart !== "delta_bars",
    },
  );
  const matrix = useMemo(() => {
    const m = q.data;
    if (!m || !ctx.keepIds) return m;
    return {
      ...m,
      rows: (m.rows ?? []).filter((r) => ctx.keepIds.has(r.row_id)),
    };
  }, [q.data, ctx.keepIds]);
  const pivot = useMemo(
    () =>
      matrix && ctx.columns.length
        ? buildPivot(matrix, {
            rows: card.rows,
            columns: ctx.columns,
            measure,
            agg: "avg",
            k: ctx.k,
            isCheck: ctx.isCheck,
          })
        : null,
    [matrix, card.rows, ctx.columns, measure, ctx.k, ctx.isCheck],
  );
  if (card.chart === "delta_bars") {
    // The Summary's own numbers: R5 vs_baseline per metric, one candidate.
    const candId =
      ctx.candidates.find((c) => c.id === cand)?.id ??
      ctx.candidateId ??
      ctx.candidates[0]?.id ??
      null;
    if (!ctx.baselineId || !candId || !ctx.analysis)
      return <Note>Delta bars need a baseline and a candidate run.</Note>;
    return (
      <DeltaBarsChart
        analysis={ctx.analysis}
        candidateId={candId}
        candidates={ctx.candidates}
        onCand={setCand}
        baseLabel={ctx.labelOf(ctx.baselineId)}
        candLabel={ctx.labelOf(candId)}
      />
    );
  }
  if (!pivot) return <Note>Loading…</Note>;
  const groups = sortedRowKeys(pivot, "default");
  const open = (g, col, cell) => {
    const fails = measure.kind === "rate" && cell.failRowIds?.length;
    ctx.onOpenRows(
      fails ? cell.failRowIds : cell.rowIds,
      `${title} · ${card.rows === "none" ? "" : `${g} · `}${col.label}${fails ? " fails" : ""}`,
    );
  };
  if (card.chart === "distribution")
    return (
      <DistributionChart
        matrix={matrix}
        columns={ctx.columns}
        measure={measure}
        colColors={ctx.colColors}
        onOpenRows={ctx.onOpenRows}
        title={title}
      />
    );
  if (card.chart === "scatter2") {
    const baseColumn =
      ctx.columns.find((c) => c.runIds.includes(ctx.baselineId)) ??
      ctx.columns[0];
    const candColumns = ctx.columns.filter(
      (c) => c.runIds.length === 1 && c.key !== baseColumn?.key,
    );
    if (!baseColumn || !candColumns.length)
      return (
        <Note>
          Two runs (scatter) needs a baseline and one more run in view.
        </Note>
      );
    return (
      <TwoRunScatterChart
        matrix={matrix}
        measure={measure}
        baseCol={baseColumn}
        candCol={candColumns.find((c) => c.key === cand) ?? candColumns[0]}
        candidates={candColumns}
        onCand={setCand}
        colColors={ctx.colColors}
        onOpenRows={ctx.onOpenRows}
      />
    );
  }
  if (card.chart === "trend") {
    if (ctx.columns.length < 2)
      return <Note>Trend needs 2 or more columns.</Note>;
    const points = trendPoints(pivot, {
      axis: ctx.colsAxis,
      runsById: ctx.runsById,
    });
    return (
      <TrendChart
        points={points}
        measure={measure}
        colColors={ctx.colColors}
        title={title}
        axisTitle={
          measure.kind === "rate" ? `${measure.short} (%)` : measure.label
        }
        caption={
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {points
              .map(
                (p) =>
                  `${p.label} ${fmtValue(measure, p.cell?.value, { precise: true })}`,
              )
              .join(" → ")}
          </Typography>
        }
        onHop={ctx.onHop}
      />
    );
  }
  if (card.chart === "heatmap")
    return (
      <HeatmapChart
        pivot={pivot}
        groups={groups}
        measure={measure}
        onOpenCell={open}
        title={title}
      />
    );
  if (card.chart === "cost_quality")
    return (
      <CostQualityChart
        matrix={matrix}
        pivot={pivot}
        columns={ctx.columns}
        measure={measure}
        colColors={ctx.colColors}
        paired={false}
        baseCol={null}
        onOpenRows={ctx.onOpenRows}
        onOpenColumn={(col) => open("All rows", col, pivot.colTotals[col.key])}
        title={title}
      />
    );
  return (
    <GroupBarsChart
      pivot={pivot}
      groups={groups}
      measure={measure}
      colColors={ctx.colColors}
      onOpenCell={open}
      title={title}
      axisTitle={
        measure.kind === "rate" ? `${measure.short} (%)` : measure.label
      }
    />
  );
}

BoardCardChart.propTypes = {
  card: PropTypes.object.isRequired,
  title: PropTypes.string,
  ctx: PropTypes.object.isRequired,
};

export default BoardCardChart;
