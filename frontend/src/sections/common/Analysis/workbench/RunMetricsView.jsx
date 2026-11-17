// Explore › Run metrics (W5-4 B2, AG-12): the dataset's run metrics
// (formulas over each run's numbers) on the runs in view, best green and
// worst red by the metric's direction, each value with the parts it read,
// plus `New run metric`, Edit and Remove. Values come from the server
// (w5DerivedMetrics.js over R1 aggregates), never from this page.
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import DerivedMetricDialog from "src/sections/common/DerivedMetrics/DerivedMetricDialog";
import {
  useDerivedMetrics,
  useSaveDerivedMetrics,
} from "src/sections/common/DerivedMetrics/useDerivedMetrics";
import {
  fmtMetric,
  partsLine,
  rankRuns,
} from "src/sections/common/DerivedMetrics/formulaModel";

const cell = { px: 1.25, py: 0.75, borderBottom: 1, borderColor: "divider" };

export default function RunMetricsView({ datasetId, runs, metricDefs }) {
  const runIds = runs.map((r) => r.run_id);
  const q = useDerivedMetrics(datasetId, runIds);
  const save = useSaveDerivedMetrics(datasetId);
  const [dialog, setDialog] = useState(null);
  const metrics = q.data?.metrics ?? [];
  const values = q.data?.values ?? {};
  return (
    <Stack gap={1.5} sx={{ p: 0.5 }} data-testid="run-metrics">
      <Stack direction="row" alignItems="center" gap={1.5}>
        <Typography typography="s1" fontWeight={600}>
          Run metrics
        </Typography>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="mdi:plus" width={16} />}
          onClick={() => setDialog({ editing: null })}
        >
          New run metric
        </Button>
        <Typography typography="s2" color="text.secondary">
          A formula over each run&apos;s eval scores, cost, latency, tokens and
          errors, saved on this dataset.
        </Typography>
      </Stack>
      {!metrics.length && q.isSuccess && (
        <Typography typography="s2" color="text.secondary">
          No run metrics on this dataset yet.
        </Typography>
      )}
      {metrics.length > 0 && (
        <Box
          component="table"
          sx={{ borderCollapse: "collapse", typography: "s2", width: "100%" }}
        >
          <thead>
            <tr>
              {["Metric", ...runs.map((r) => r.label), ""].map((h, i) => (
                <Box
                  component="th"
                  key={`${h}-${i}`}
                  sx={{ ...cell, textAlign: "left", color: "text.secondary" }}
                >
                  {h}
                </Box>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => {
              const { best, worst } = rankRuns(values, m);
              const err = q.data?.errors?.[m.id];
              return (
                <tr key={m.id} data-testid="run-metric-row" data-name={m.name}>
                  <Box component="td" sx={cell}>
                    <Typography typography="s2" fontWeight={600}>
                      {m.name}
                    </Typography>
                    <Typography
                      typography="s3"
                      color="text.secondary"
                      sx={{ fontFamily: "monospace" }}
                    >
                      {`${m.formula} · ${m.higher_is_better === false ? "lower" : "higher"} is better`}
                    </Typography>
                  </Box>
                  {runs.map((r) => {
                    const v = values[r.run_id]?.[m.id];
                    const tone =
                      r.run_id === best
                        ? "green.600"
                        : r.run_id === worst
                          ? "red.600"
                          : "text.primary";
                    return (
                      <Box
                        component="td"
                        key={r.run_id}
                        sx={cell}
                        data-run={r.label}
                        data-value={v?.value ?? ""}
                      >
                        <Tooltip
                          title={v ? partsLine(m.formula, v, m.format) : ""}
                        >
                          <Typography
                            typography="s1"
                            color={tone}
                            sx={{ fontVariantNumeric: "tabular-nums" }}
                          >
                            {err ? "—" : fmtMetric(v?.value, m.format)}
                          </Typography>
                        </Tooltip>
                        {v && (
                          <Typography
                            typography="s3"
                            color="text.secondary"
                            sx={{ fontVariantNumeric: "tabular-nums" }}
                          >
                            {partsLine(m.formula, v, m.format).split(" = ")[1]}
                          </Typography>
                        )}
                      </Box>
                    );
                  })}
                  <Box component="td" sx={{ ...cell, whiteSpace: "nowrap" }}>
                    {err && (
                      <Typography typography="s3" color="error.main">
                        {err}
                      </Typography>
                    )}
                    <IconButton
                      size="small"
                      aria-label={`Edit ${m.name}`}
                      onClick={() => setDialog({ editing: m })}
                    >
                      <Iconify icon="mdi:pencil-outline" width={16} />
                    </IconButton>
                    <IconButton
                      size="small"
                      aria-label={`Remove ${m.name}`}
                      disabled={save.isPending}
                      onClick={() =>
                        save.mutate(metrics.filter((x) => x.id !== m.id))
                      }
                    >
                      <Iconify icon="mdi:delete-outline" width={16} />
                    </IconButton>
                  </Box>
                </tr>
              );
            })}
          </tbody>
        </Box>
      )}
      {dialog && (
        <DerivedMetricDialog
          open
          onClose={() => setDialog(null)}
          datasetId={datasetId}
          runs={runs}
          metricDefs={metricDefs}
          existing={metrics}
          editing={dialog.editing}
        />
      )}
    </Stack>
  );
}

RunMetricsView.propTypes = {
  datasetId: PropTypes.string.isRequired,
  runs: PropTypes.array.isRequired,
  metricDefs: PropTypes.array.isRequired,
};
