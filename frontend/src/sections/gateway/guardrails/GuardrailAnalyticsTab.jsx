import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";

import { GatewayEmptyState, GatewayGrid } from "../components/frame";
import { catalogEntry } from "./catalog";
import {
  useGuardrailOverview,
  useGuardrailRules,
  useGuardrailTrends,
} from "./hooks/useGuardrailAnalytics";
import StatStrip from "./rules/StatStrip";
import RangeToggle from "../analytics/RangeToggle";
import { EMPTY } from "../utils/formatters";
import { GUARDRAILS_OFF_TITLE } from "../utils/guardrailPipeline";
import { useGuardrailsOff } from "./hooks/useGuardrailsOff";
import {
  bucketTime,
  localTimeAxis,
  localTimeTooltip,
  zoneNote,
} from "../utils/timeZone";

const RANGES = [
  // Labelled as the Analytics page's ranges (analytics/analyticsRange.js).
  { value: "24h", label: "24H", title: "Last 24 hours", hours: 24 },
  { value: "7d", label: "7D", title: "Last 7 days", hours: 24 * 7 },
  { value: "30d", label: "30D", title: "Last 30 days", hours: 24 * 30 },
];

function windowFor(range) {
  const end = new Date();
  end.setSeconds(0, 0);
  const start = new Date(end.getTime() - range.hours * 3600 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

const count = (value) =>
  value === undefined || value === null
    ? EMPTY
    : Number(value).toLocaleString();
const rate = (value) =>
  value === undefined || value === null ? null : `${Number(value).toFixed(1)}%`;

const RULE_COLUMNS = [
  {
    field: "label",
    headerName: "Rule",
    minWidth: 200,
    flex: 2,
  },
  {
    field: "triggers",
    headerName: "Matches",
    minWidth: 110,
    flex: 0,
    width: 120,
    type: "rightAligned",
  },
  {
    field: "blocked",
    headerName: "Blocked",
    minWidth: 110,
    flex: 0,
    width: 120,
    type: "rightAligned",
  },
  {
    field: "warned",
    headerName: "Warned",
    minWidth: 110,
    flex: 0,
    width: 120,
    type: "rightAligned",
  },
  {
    field: "share",
    headerName: "Share of matches",
    valueFormatter: ({ value }) => `${value.toFixed(1)}%`,
    minWidth: 160,
    flex: 0,
    width: 190,
    type: "rightAligned",
  },
];

/**
 * What the org's guardrails matched over a window: a strip of totals, matches
 * over time and the rules ranked. The gateway does not time a check, so there
 * is no latency here; the trends endpoint returns matches per bucket only, so
 * that is the one series charted.
 */
const GuardrailAnalyticsTab = ({ gatewayId }) => {
  // No check runs on a gateway with guardrails off, so nothing is matched.
  const off = useGuardrailsOff(gatewayId);
  const theme = useTheme();
  const [rangeId, setRangeId] = useState("7d");
  const range = RANGES.find((item) => item.value === rangeId);
  const params = useMemo(() => windowFor(range), [range]);
  const overview = useGuardrailOverview(params);
  const rules = useGuardrailRules(params);
  const granularity = range.hours <= 24 ? "hour" : "day";
  const trends = useGuardrailTrends({ ...params, granularity });

  const rows = useMemo(() => {
    const list = Array.isArray(rules.data) ? rules.data : [];
    const total = list.reduce(
      (sum, r) => sum + Number(r.trigger_count || 0),
      0,
    );
    return list.map((r) => {
      const name = r.rule || r.name;
      const triggers = Number(r.trigger_count || 0);
      return {
        id: name,
        label: catalogEntry(name)?.label || name,
        triggers,
        blocked: Number(r.block_count || 0),
        warned: Number(r.warn_count || 0),
        share: total > 0 ? (triggers / total) * 100 : 0,
      };
    });
  }, [rules.data]);

  const series = useMemo(
    () => (Array.isArray(trends.data?.series) ? trends.data.series : []),
    [trends.data],
  );
  // Day buckets read in the zone the API cut them in.
  const { day, utc } = bucketTime(granularity, trends.data);
  const chartOptions = useMemo(
    () => ({
      chart: {
        id: "guardrail-matches",
        toolbar: { show: false },
        zoom: { enabled: false },
        fontFamily: theme.typography.fontFamily,
        background: "transparent",
      },
      theme: { mode: theme.palette.mode },
      xaxis: localTimeAxis({
        color: theme.palette.text.secondary,
        utc,
        categories: series.map((point) => point.timestamp || point.bucket),
      }),
      yaxis: {
        min: 0,
        forceNiceScale: true,
        labels: {
          style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        },
      },
      stroke: { curve: "straight", width: 2 },
      fill: { type: "solid", opacity: 0.08 },
      dataLabels: { enabled: false },
      colors: [theme.palette.primary.main],
      tooltip: {
        theme: theme.palette.mode,
        x: localTimeTooltip({ day, utc }),
      },
      grid: { borderColor: theme.palette.divider, strokeDashArray: 3 },
      legend: { show: false },
    }),
    [series, theme, day, utc],
  );

  const hasHistory =
    Number(overview.data?.total_requests || 0) > 0 ||
    rows.some((row) => row.triggers > 0) ||
    series.some((point) => Number(point.trigger_count || 0) > 0);
  if (off && !hasHistory && !overview.isError && !rules.isError) {
    return (
      <Box>
        <RangeToggle value={rangeId} options={RANGES} onChange={setRangeId} />
        <GatewayEmptyState
          icon="analytics"
          title={GUARDRAILS_OFF_TITLE}
          description="No check runs now. No historical matches are recorded in this window."
        />
      </Box>
    );
  }

  if (overview.isError && rules.isError) {
    return (
      <GatewayEmptyState
        icon="analytics"
        title="Guardrail analytics could not be loaded"
        description="The analytics service did not answer. Your rules are unaffected."
        actionLabel="Retry"
        onAction={() => {
          overview.refetch();
          rules.refetch();
          trends.refetch();
        }}
      />
    );
  }

  const o = overview.data || {};
  const quiet = !overview.isLoading && Number(o.total_requests || 0) === 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {off && (
        <Typography typography="s2">
          {GUARDRAILS_OFF_TITLE}. No check runs now; recorded history remains
          available.
        </Typography>
      )}
      <RangeToggle value={rangeId} options={RANGES} onChange={setRangeId} />

      <StatStrip
        label={range.title}
        loading={overview.isLoading}
        error={overview.isError}
        items={[
          { label: "Requests", value: count(o.total_requests) },
          {
            label: "Matched",
            value: count(o.guardrail_triggered),
            detail: rate(o.trigger_rate),
          },
          {
            label: "Blocked",
            value: count(o.block_count),
            detail: rate(o.block_rate),
          },
          {
            label: "Warned",
            value: count(o.warn_count),
            detail: rate(o.warn_rate),
          },
        ]}
      />

      {quiet ? (
        <GatewayEmptyState
          icon="analytics"
          title="No requests in this window"
          description="Matches appear here once requests go through the gateway."
        />
      ) : (
        <>
          <Box
            component="section"
            aria-label="Matches over time"
            sx={{
              p: 2,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              backgroundColor: "background.paper",
            }}
          >
            <Typography typography="s1" fontWeight="fontWeightMedium">
              Matches over time
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {zoneNote({ utcDays: utc })}
            </Typography>
            {trends.isLoading ? (
              <Skeleton variant="rounded" height={240} sx={{ mt: 1 }} />
            ) : trends.isError ? (
              <Typography typography="s2" color="text.secondary" sx={{ py: 6 }}>
                The trend could not be loaded.
              </Typography>
            ) : (
              <Chart
                options={chartOptions}
                series={[
                  {
                    name: "Matches",
                    data: series.map((point) =>
                      Number(point.trigger_count || 0),
                    ),
                  },
                ]}
                type="area"
                height={240}
              />
            )}
          </Box>

          <Typography typography="s1" fontWeight="fontWeightMedium">
            Rules by matches
          </Typography>
          <GatewayGrid
            rowData={rules.isLoading ? undefined : rows}
            loading={rules.isLoading}
            columnDefs={RULE_COLUMNS}
            getRowId={(p) => p.data.id}
            stateKey="gateway-guardrail-analytics-rules"
            height="auto"
            domLayout="autoHeight"
            emptyState={{
              icon: "guardrails",
              title: "No rule matched in this window",
            }}
          />
        </>
      )}
    </Box>
  );
};

GuardrailAnalyticsTab.propTypes = {
  gatewayId: PropTypes.string,
};

export default GuardrailAnalyticsTab;
