import PropTypes from "prop-types";
import { Alert, Box, Button, Divider, Stack, Typography } from "@mui/material";
import { useMetricKpi, useMetricTrend } from "./api";
import { metricCopy, metricCountCopy } from "./copy";
import { formatMetricInterval, formatMetricValue } from "./formatMetricValue";
import { formatMetricWindow } from "./metricTime";
import { pointValueState } from "./unitValueState";
import MetricValueCell from "./MetricValueCell";
import MetricStatusLine, { MetricReadNotice } from "./MetricStatusLine";
import MetricTrendChart from "./MetricTrendChart";

import { formatMetricDifference, sameMetricRead } from "./metricPageValues";

function PreviousPeriod({ data }) {
  const current = pointValueState(data.current);
  const previous = pointValueState(data.previous);
  const canCompare = current.state === "value" && previous.state === "value";
  const delta = canCompare
    ? formatMetricDifference(data.delta, data.current)
    : null;
  const end = Date.parse(data.effective_window.window_start);
  const duration = Date.parse(data.effective_window.window_end) - end;
  const previousWindow =
    Number.isFinite(end) && duration > 0
      ? {
          window_start: new Date(end - duration).toISOString(),
          window_end: new Date(end).toISOString(),
        }
      : null;
  const uncertainty = data.delta_uncertainty;
  const hasInterval =
    delta &&
    uncertainty?.status === "ok" &&
    Number.isFinite(uncertainty.ci_low) &&
    Number.isFinite(uncertainty.ci_high) &&
    uncertainty.ci_low <= uncertainty.ci_high;
  return (
    <Stack spacing={1.5} sx={{ minWidth: 280, maxWidth: 440 }}>
      <Typography variant="body2" fontWeight={600}>
        Previous period
      </Typography>
      {previousWindow && (
        <Typography variant="caption" color="text.secondary">
          {formatMetricWindow(previousWindow)}
        </Typography>
      )}
      {data.previous ? (
        <MetricValueCell source="point" value={data.previous} />
      ) : (
        <Typography variant="body2" color="text.secondary">
          Previous period unavailable
        </Typography>
      )}
      <Typography variant="body2">
        {delta ? `${delta} vs previous period` : "Comparison unavailable"}
      </Typography>
      {delta && (
        <Typography variant="caption" color="text.secondary">
          {hasInterval
            ? `Difference interval: ${formatMetricDifference(uncertainty.ci_low, data.current)} to ${formatMetricDifference(uncertainty.ci_high, data.current)}`
            : uncertainty?.reason === "paired_covariance_unavailable"
              ? "Difference interval unavailable: paired covariance is not available."
              : "Difference interval unavailable."}
        </Typography>
      )}
    </Stack>
  );
}
PreviousPeriod.propTypes = { data: PropTypes.object.isRequired };

export default function MetricOverview({ metric, scope, window }) {
  const args = {
    scope,
    metricId: metric?.id,
    version: metric?.definition?.version,
    params: window,
  };
  const available =
    Boolean(metric?.definition) && metric.definition.level !== "user";
  const kpi = useMetricKpi({ ...args, enabled: available });
  const trend = useMetricTrend({
    ...args,
    enabled: available,
    params: { ...window, points: 30 },
  });
  if (metric?.definition?.level === "user")
    return (
      <Alert severity="info">
        User-level metrics are not available yet. No user values or estimates
        have been calculated.
      </Alert>
    );
  if (metric && !metric.definition)
    return <MetricStatusLine definition={metric.draft_definition} />;
  const data = kpi.data;
  const state = pointValueState(data?.current);
  const hasValue = state.state === "value";
  const current = data?.current;
  const mismatch = data && trend.data && !sameMetricRead(data, trend.data);
  return (
    <Stack spacing={3}>
      <MetricReadNotice query={kpi} label="Metric value" />
      {data && (
        <>
          <Stack
            direction="row"
            spacing={4}
            alignItems="flex-start"
            divider={<Divider orientation="vertical" flexItem />}
          >
            <Stack spacing={2} sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="body2" color="text.secondary">
                {formatMetricWindow(data.effective_window)}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {data.effective_window.rounding}
              </Typography>
              <Box>
                <Typography
                  component="p"
                  data-testid="metric-hero"
                  sx={{
                    fontSize: hasValue ? 44 : 24,
                    lineHeight: 1.2,
                    fontWeight: 600,
                    letterSpacing: "-0.02em",
                    color: hasValue ? "text.primary" : "text.secondary",
                    overflowWrap: "anywhere",
                  }}
                >
                  {hasValue
                    ? `${current.weighted ? "≈ " : ""}${formatMetricValue(current.value, { ...current, value_type: "number" })}`
                    : state.label}
                </Typography>
                {hasValue && current.weighted && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 1 }}
                  >
                    Estimate · {metricCopy.estimateExplanation}
                  </Typography>
                )}
              </Box>
              {hasValue && (
                <Typography variant="body2" color="text.secondary">
                  {formatMetricInterval(current)}
                </Typography>
              )}
              {metricCountCopy(current, current?.unit_noun).map((line) => (
                <Typography key={line} variant="body2" color="text.secondary">
                  {line}
                </Typography>
              ))}
            </Stack>
            <PreviousPeriod data={data} />
          </Stack>
          <MetricStatusLine
            provenance={data.provenance}
            definition={metric.definition}
          />
        </>
      )}
      <Box sx={{ pt: 3, borderTop: 1, borderColor: "divider" }}>
        <MetricReadNotice query={trend} label="Trend" />
        {mismatch && (
          <Alert
            severity="info"
            action={
              <Button
                color="inherit"
                onClick={() => {
                  void kpi.refetch();
                  void trend.refetch();
                }}
              >
                Refresh values
              </Button>
            }
          >
            The metric updated between reads. Refresh to align the KPI and
            trend.
          </Alert>
        )}
        <MetricTrendChart
          data={mismatch ? undefined : trend.data}
          name={metric?.name}
        />
      </Box>
    </Stack>
  );
}
MetricOverview.propTypes = {
  metric: PropTypes.object,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object.isRequired,
};
