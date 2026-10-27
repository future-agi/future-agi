import React, { useState, useMemo } from "react";
import {
  Box,
  Typography,
  Stack,
  Card,
  CardContent,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  ToggleButton,
  ToggleButtonGroup,
  Skeleton,
  LinearProgress,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import PropTypes from "prop-types";
import {
  useGuardrailOverview,
  useGuardrailRules,
  useGuardrailTrends,
} from "./hooks/useGuardrailAnalytics";
import Chart from "react-apexcharts";

const RANGE_FILTERS = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
];

function getDateRange(range) {
  const end = new Date().toISOString();
  const start = new Date();
  if (range === "24h") start.setHours(start.getHours() - 24);
  else if (range === "7d") start.setDate(start.getDate() - 7);
  else start.setDate(start.getDate() - 30);
  return { start: start.toISOString(), end };
}

function formatPct(val) {
  if (val == null) return "0%";
  return `${(Number(val) * 100).toFixed(1)}%`;
}

function formatPctDirect(val) {
  if (val == null) return "0%";
  // Value is already a percentage (e.g. 21.09 means 21.09%)
  return `${Number(val).toFixed(1)}%`;
}

function getOverviewValue(overview, camelKey, snakeKey) {
  return overview?.[camelKey] ?? overview?.[snakeKey];
}

function getRuleCount(rule, camelKey, snakeKey) {
  return rule?.[camelKey] ?? rule?.[snakeKey] ?? 0;
}

const GuardrailAnalyticsTab = ({ gatewayId }) => {
  const [range, setRange] = useState("7d");
  const theme = useTheme();
  const dateRange = useMemo(() => getDateRange(range), [range]);
  const granularity = range === "24h" ? "hour" : "day";

  const params = { gateway_id: gatewayId, ...dateRange };
  const { data: overview, isLoading: ovLoading } = useGuardrailOverview(params);
  const { data: rules, isLoading: rulesLoading } = useGuardrailRules(params);
  const { data: trends, isLoading: trendsLoading } = useGuardrailTrends({
    ...params,
    granularity,
  });

  const trendList = Array.isArray(trends) ? trends : [];
  const avgLatency =
    overview?.avg_guardrail_latency_ms ??
    overview?.avgGuardrailLatencyMs ??
    overview?.avg_latency;

  const chartOptions = useMemo(
    () => ({
      chart: {
        id: "guardrail-trends",
        toolbar: { show: false },
        zoom: { enabled: false },
        fontFamily: theme.typography.fontFamily,
        background: "transparent",
      },
      theme: { mode: theme.palette.mode },
      xaxis: {
        type: "datetime",
        categories: trendList.map((t) => t.timestamp || t.bucket),
        labels: {
          style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        },
      },
      yaxis: {
        labels: {
          style: { colors: theme.palette.text.secondary, fontSize: "11px" },
        },
      },
      stroke: { curve: "smooth", width: 2 },
      dataLabels: { enabled: false },
      colors: [
        theme.palette.error.main,
        theme.palette.warning.main,
        theme.palette.info.main,
      ],
      tooltip: {
        theme: theme.palette.mode,
        x: { format: "MMM dd HH:mm" },
      },
      grid: {
        borderColor: theme.palette.divider,
        strokeDashArray: 3,
      },
      legend: {
        position: "top",
        labels: { colors: theme.palette.text.secondary },
      },
    }),
    [trendList, theme],
  );

  const chartSeries = useMemo(
    () => [
      {
        name: "Blocked",
        data: trendList.map(
          (t) => t.blockCount ?? t.block_count ?? t.blocked ?? 0,
        ),
      },
      {
        name: "Warned",
        data: trendList.map(
          (t) => t.warnCount ?? t.warn_count ?? t.warned ?? 0,
        ),
      },
      {
        name: "Total Triggered",
        data: trendList.map((t) => t.trigger_count || t.total || 0),
      },
    ],
    [trendList],
  );

  if (ovLoading) {
    return (
      <Stack spacing={2}>
        <Stack direction="row" spacing={2}>
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} variant="rounded" width="25%" height={100} />
          ))}
        </Stack>
        <Skeleton variant="rounded" width="100%" height={300} />
      </Stack>
    );
  }

  return (
    <Stack spacing={3}>
      <Stack direction="row" justifyContent="flex-end">
        <ToggleButtonGroup
          value={range}
          exclusive
          onChange={(_, v) => v && setRange(v)}
          size="small"
        >
          {RANGE_FILTERS.map((f) => (
            <ToggleButton
              key={f.value}
              value={f.value}
              sx={{ px: 1.5, py: 0.25, textTransform: "none" }}
            >
              {f.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      {/* KPI Cards */}
      <Grid container spacing={2}>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Trigger Rate
              </Typography>
              <Typography variant="h4">
                {formatPctDirect(
                  getOverviewValue(overview, "triggerRate", "trigger_rate"),
                )}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Blocked
              </Typography>
              <Typography variant="h4" color="error.main">
                {getOverviewValue(overview, "blockCount", "block_count") ?? 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Warned
              </Typography>
              <Typography variant="h4" color="warning.main">
                {getOverviewValue(overview, "warnCount", "warn_count") ?? 0}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Avg Latency
              </Typography>
              <Typography variant="h4">
                {avgLatency != null
                  ? `${Number(avgLatency).toFixed(0)}ms`
                  : "\u2014"}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Trends chart */}
      <Card>
        <CardContent>
          <Typography variant="subtitle1" mb={2}>
            Guardrail Triggers Over Time
          </Typography>
          {trendsLoading ? (
            <Skeleton variant="rounded" width="100%" height={280} />
          ) : trendList.length > 0 ? (
            <Chart
              options={chartOptions}
              series={chartSeries}
              type="area"
              height={280}
            />
          ) : (
            <Box
              display="flex"
              justifyContent="center"
              alignItems="center"
              height={200}
            >
              <Typography color="text.secondary">
                No trend data available
              </Typography>
            </Box>
          )}
        </CardContent>
      </Card>

      {/* Rule leaderboard */}
      <Card>
        <CardContent>
          <Typography variant="subtitle1" mb={2}>
            Top Triggered Rules
          </Typography>
          {rulesLoading ? (
            <Stack spacing={1}>
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} width="100%" height={36} />
              ))}
            </Stack>
          ) : !rules?.length ? (
            <Typography color="text.secondary" textAlign="center" py={3}>
              No rule data available
            </Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Rule</TableCell>
                    <TableCell align="right">Triggers</TableCell>
                    <TableCell align="right">Block</TableCell>
                    <TableCell align="right">Warn</TableCell>
                    <TableCell>Share</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {rules.map((rule) => {
                    const totalTriggers = rules.reduce(
                      (acc, r) => acc + (r.trigger_count || r.count || 0),
                      0,
                    );
                    const count = rule.trigger_count || rule.count || 0;
                    const blockCount = getRuleCount(
                      rule,
                      "blockCount",
                      "block_count",
                    );
                    const warnCount = getRuleCount(
                      rule,
                      "warnCount",
                      "warn_count",
                    );
                    const share = totalTriggers > 0 ? count / totalTriggers : 0;
                    const ruleName = rule.rule || rule.name;
                    return (
                      <TableRow key={ruleName} hover>
                        <TableCell>
                          <Typography variant="body2" fontWeight={500}>
                            {ruleName}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography variant="body2">{count}</Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Chip
                            label={blockCount}
                            color="error"
                            size="small"
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell align="right">
                          <Chip
                            label={warnCount}
                            color="warning"
                            size="small"
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell sx={{ width: 150 }}>
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={1}
                          >
                            <LinearProgress
                              variant="determinate"
                              value={share * 100}
                              sx={{ flex: 1, height: 6, borderRadius: 3 }}
                            />
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ minWidth: 36 }}
                            >
                              {formatPct(share)}
                            </Typography>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
};

GuardrailAnalyticsTab.propTypes = {
  gatewayId: PropTypes.string,
};

export default GuardrailAnalyticsTab;
