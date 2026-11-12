import PropTypes from "prop-types";
import { useMemo } from "react";
import { MenuItem, TextField } from "@mui/material";

import SectionCard from "../../../components/SectionCard";
import { Bars, NoMeasurement } from "../detail/analytics/DashboardCharts";
import { COMPARE_COPY } from "../../workspace.constants";

export const SCORE_BANDS = Array.from(
  { length: 10 },
  (_, i) => `${i * 10}–${(i + 1) * 10}%`,
);

/**
 * One row per score band, one count per run, from the runs' histograms.
 * @param {Array<{ id: string, histograms: Object }>} runs
 * @param {string} evalId
 */
export const histogramRows = (runs, evalId) =>
  SCORE_BANDS.map((label, band) => ({
    label,
    ...Object.fromEntries(
      runs.map((run) => [run.id, run.histograms?.[evalId]?.[band] ?? 0]),
    ),
  }));

// Each run's calls over ten score bands, for one evaluation, side by side in
// the runs' colours.
export default function CompareScoreChart({
  runs,
  evals,
  evalId,
  onEvalChange,
}) {
  const rows = useMemo(() => histogramRows(runs, evalId), [runs, evalId]);
  const series = runs.map((run) => ({
    key: run.id,
    label: `${run.ordinal} · ${COMPARE_COPY.runLabel(run.agentLabel, run.environmentLabel)}`,
    colorIndex: run.colorIndex,
  }));
  return (
    <SectionCard
      title={COMPARE_COPY.chartTitle}
      // Keeps its height above the table, which scrolls in what is left.
      sx={{ flexShrink: 0 }}
      action={
        evals.length > 0 && (
          <TextField
            select
            size="small"
            value={evalId ?? ""}
            onChange={(e) => onEvalChange(e.target.value)}
            inputProps={{ "aria-label": COMPARE_COPY.chartEval }}
            sx={{ width: 220 }}
          >
            {evals.map((e) => (
              <MenuItem key={e.id} value={e.id}>
                {e.name}
              </MenuItem>
            ))}
          </TextField>
        )
      }
    >
      {evalId ? (
        <Bars
          rows={rows}
          xKey="label"
          series={series}
          stacked={false}
          legend
          height={220}
        />
      ) : (
        <NoMeasurement text={COMPARE_COPY.noScores} />
      )}
    </SectionCard>
  );
}
CompareScoreChart.propTypes = {
  runs: PropTypes.array.isRequired,
  evals: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, name: PropTypes.string }),
  ).isRequired,
  evalId: PropTypes.string,
  onEvalChange: PropTypes.func.isRequired,
};
