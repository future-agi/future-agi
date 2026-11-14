import PropTypes from "prop-types";
import { Alert, Box, Button, Stack, Typography } from "@mui/material";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import { useMetricKpi, useMetricTrend } from "./api";
import MetricFirstCalculation from "./MetricFirstCalculation";
import { forwardPendingCopy, metricCopy, metricCountCopy } from "./copy";
import { formatMetricInterval, formatMetricValue } from "./formatMetricValue";
import { coveragePending, formatMetricWindow } from "./metricTime";
import { pointValueState } from "./unitValueState";
import MetricValueCell from "./MetricValueCell";
import MetricStatusLine, {
  MetricCoverageLine,
  MetricReadNotice,
} from "./MetricStatusLine";
import MetricTrendChart from "./MetricTrendChart";

import { formatMetricDifference, sameMetricRead } from "./metricPageValues";

// The KPI read has not settled yet; its card may still state the window.
const KPI_PENDING = ["idle", "loading"];

function previousPeriod(data) {
  const current = pointValueState(data.current);
  const previous = pointValueState(data.previous);
  const canCompare = current.state === "value" && previous.state === "value";
  return canCompare ? formatMetricDifference(data.delta, data.current) : null;
}

function PreviousPeriod({ data }) {
  const delta = previousPeriod(data);
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
    <Stack
      spacing={1}
      sx={{ p: 2.5, minWidth: 0, borderLeft: 1, borderColor: "divider" }}
    >
      <Typography variant="subtitle2" color="text.secondary">
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

function Change({ data }) {
  const delta = previousPeriod(data);
  if (!delta)
    return (
      <Typography variant="body2" color="text.secondary">
        Comparison unavailable
      </Typography>
    );
  // Whether up is good depends on the metric, so the change stays neutral.
  const icon =
    data.delta > 0
      ? "solar:arrow-right-up-linear"
      : data.delta < 0
        ? "solar:arrow-right-down-linear"
        : "solar:arrow-right-linear";
  return (
    <Stack direction="row" spacing={0.5} alignItems="center">
      <Iconify icon={icon} width={16} sx={{ color: "text.secondary" }} />
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {`${delta} vs previous period`}
      </Typography>
    </Stack>
  );
}
Change.propTypes = { data: PropTypes.object.isRequired };

export default function MetricOverview({
  metric,
  scope,
  window,
  windowLabel,
  onViewHistory,
}) {
  const args = {
    scope,
    metricId: metric?.id,
    version: metric?.definition?.version,
    params: window,
  };
  // A metric that started with no history has no full 15-minute period to
  // read yet; the server cannot read less, so no read is sent.
  const waiting = coveragePending(window, metric?.definition);
  const available =
    Boolean(metric?.definition) && metric.definition.level !== "user";
  const kpi = useMetricKpi({ ...args, enabled: available && !waiting });
  const trend = useMetricTrend({
    ...args,
    enabled: available && !waiting,
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
    return (
      <MetricFirstCalculation
        metric={metric}
        scope={scope}
        onViewHistory={onViewHistory}
      />
    );
  if (waiting)
    return (
      <Stack spacing={1.5} data-testid="metric-coverage-pending">
        <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
          <EmptyState
            variant="no-results"
            icon="solar:clock-circle-linear"
            title="Not calculated yet"
            description={forwardPendingCopy(metric.definition)}
          />
        </Box>
        <MetricCoverageLine definition={metric.definition} />
      </Stack>
    );
  const data = kpi.data;
  const state = pointValueState(data?.current);
  const hasValue = state.state === "value";
  const current = data?.current;
  const mismatch = data && trend.data && !sameMetricRead(data, trend.data);
  return (
    <Stack spacing={2.5}>
      <MetricReadNotice
        query={kpi}
        label="Metric value"
        definition={metric?.definition}
      />
      {/* With values the coverage line sits in the status footer below;
          without them it still says where values begin. */}
      {!data && <MetricCoverageLine definition={metric?.definition} />}
      {data && (
        <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.6fr) minmax(300px, 1fr)",
            }}
          >
            <Stack spacing={1} sx={{ p: 2.5, minWidth: 0 }}>
              <Typography variant="subtitle2" color="text.secondary">
                {windowLabel || "Selected window"}
              </Typography>
              <Stack
                direction="row"
                alignItems="baseline"
                columnGap={2}
                rowGap={0.5}
                flexWrap="wrap"
              >
                <Typography
                  component="p"
                  data-testid="metric-hero"
                  variant={hasValue ? "display" : "title_md"}
                  color={hasValue ? "text.primary" : "text.secondary"}
                  // Rounded for reading; the unrounded value is on hover.
                  title={
                    hasValue
                      ? formatMetricValue(current.value, {
                          ...current,
                          value_type: "number",
                        }) ?? undefined
                      : undefined
                  }
                  sx={{
                    fontVariantNumeric: "tabular-nums",
                    overflowWrap: "anywhere",
                  }}
                >
                  {hasValue
                    ? `${current.weighted ? "≈ " : ""}${formatMetricValue(current.value, { ...current, value_type: "number" }, undefined, { display: true })}`
                    : state.label}
                </Typography>
                <Change data={data} />
              </Stack>
              {hasValue && current.weighted && (
                <Typography variant="body2" color="text.secondary">
                  Estimate · {metricCopy.estimateExplanation}
                </Typography>
              )}
              {hasValue && (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {formatMetricInterval(current, undefined, { display: true })}
                </Typography>
              )}
              {metricCountCopy(current, current?.unit_noun).map((line) => (
                <Typography
                  key={line}
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontVariantNumeric: "tabular-nums" }}
                >
                  {line}
                </Typography>
              ))}
            </Stack>
            <PreviousPeriod data={data} />
          </Box>
          <Stack
            direction="row"
            alignItems="flex-start"
            justifyContent="space-between"
            spacing={3}
            sx={{
              px: 2.5,
              py: 1.25,
              borderTop: 1,
              borderColor: "divider",
            }}
          >
            <Typography variant="caption" color="text.secondary">
              {formatMetricWindow(data.effective_window)} ·{" "}
              {data.effective_window.rounding}
            </Typography>
            <Box sx={{ flexShrink: 0, maxWidth: "50%" }}>
              <MetricStatusLine
                provenance={data.provenance}
                definition={metric.definition}
                completeThrough={data.complete_through}
                partial={data.partial}
              />
            </Box>
          </Stack>
        </Box>
      )}
      <Box>
        <MetricReadNotice
          query={trend}
          label="Trend"
          definition={metric?.definition}
        />
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
        {/* The window and rounding line is stated once: in the KPI footer,
            or under the trend only once the KPI read has settled into a
            failure. While the KPI loads the line waits, so it never jumps
            between the two. A different trend window is a mismatch, which
            the alert above already covers. */}
        <MetricTrendChart
          data={mismatch ? undefined : trend.data}
          name={metric?.name}
          showWindow={!data && !KPI_PENDING.includes(kpi.readState)}
        />
      </Box>
    </Stack>
  );
}
MetricOverview.propTypes = {
  metric: PropTypes.object,
  scope: PropTypes.object.isRequired,
  window: PropTypes.object.isRequired,
  windowLabel: PropTypes.string,
  onViewHistory: PropTypes.func,
};
