import React, { useEffect, useState, useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Chart from "react-apexcharts";
import { monoSeriesColors, seriesLabelColors } from "src/theme/data-colors";
import { useAnalyticsCost } from "./hooks/useAnalyticsCost";
import CostTotal from "../components/CostTotal";
import { sumMoneyValues } from "src/sections/common/format/money";
import {
  NO_PRICE,
  costTotal,
  formatChartCost,
  unpricedNote,
} from "../utils/formatters";
import { COST_DIMENSION_OPTIONS } from "./costDimensions";
import ChartPanel, { AnalyticsQueryState } from "./ChartPanel";
import { buildLogsDrilldown } from "./logsDrilldown";
import Link from "@mui/material/Link";
import GroupByToggle from "./GroupByToggle";
import { TOP_SERIES, colorsForNames, withPaletteRest } from "./seriesColors";
import { barValueOptions } from "./barLabels";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOP_N = TOP_SERIES;

function toNumericCost(value) {
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue : 0;
}

// An unpriced request is stored at cost 0, so a group's cost is unknown when
// none of its requests had a price and a floor when some had none. The API counts each group's unpriced_requests.
const groupCost = (value, group, options) =>
  costTotal(value, {
    unpriced: group?.unpriced_requests,
    requests: group?.request_count,
    ...options,
  });

function barLabel(value, group) {
  const { text, unknown } = groupCost(value, group, { cell: true });
  return unknown ? "No price" : text;
}

// Every cost on these charts reads one way (formatChartCost, the grids'
// grouped exact amount); costTotal's cell form is that format.
function barTooltip(value, group) {
  const { text, title, unknown } = groupCost(value, group, { cell: true });
  if (unknown && title === NO_PRICE) {
    const requests = Number(group.request_count);
    return `${NO_PRICE} (${requests.toLocaleString()} ${
      requests === 1 ? "request" : "requests"
    })`;
  }
  if (unknown) return title;
  return title
    ? `${text} (${unpricedNote(group.unpriced_requests, group.request_count)})`
    : text;
}

// ---------------------------------------------------------------------------
// Chart option builders
// ---------------------------------------------------------------------------

// A value-axis tick reads like every other cost here (formatChartCost,
// grouped: "$1,000.00"). In the narrow layout (a window under 1200 px, where
// the chart's card is about half the content width and six full amounts ran
// together) it is compact from $1,000 ("$25K"); the bars' labels and the
// tooltips keep the exact amounts.
const NARROW_CHART_QUERY = "(max-width: 1199.95px)";

// The donut's total: the shown slices' exact amounts added as decimals (a
// float sum reads 0.1 + 0.2 as 0.30000000000000004). ApexCharts gives the
// slices as floats, a slice the legend hid as 0; each shown one is matched to
// its API amount by position.
function donutTotal(seriesTotals, amounts) {
  const parts = seriesTotals.map((shown, i) => {
    if (!shown) return "0";
    return Number(amounts[i]) === shown ? amounts[i] : shown;
  });
  return sumMoneyValues(parts) ?? seriesTotals.reduce((a, b) => a + b, 0);
}

// Whether the window is under 1200 px. Reads matchMedia defensively: a
// missing or stubbed matchMedia reads as wide.
function useNarrowChart() {
  const read = () => {
    try {
      return Boolean(window.matchMedia?.(NARROW_CHART_QUERY)?.matches);
    } catch {
      return false;
    }
  };
  const [narrow, setNarrow] = useState(read);
  useEffect(() => {
    let list;
    try {
      list = window.matchMedia?.(NARROW_CHART_QUERY);
    } catch {
      return undefined;
    }
    if (!list?.addEventListener) return undefined;
    const onChange = () => setNarrow(Boolean(list.matches));
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

function buildBarChartOptions(
  theme,
  categories,
  colors,
  groups = [],
  narrow = false,
) {
  // Each value past its bar's end (barLabels.js): a cell's two significant
  // figures, the tooltip has the exact amount; a group with no priced
  // request reads "No price", one with some a floor.
  const values = barValueOptions(theme, (val, opts) =>
    barLabel(val, groups[opts?.dataPointIndex]),
  );
  return {
    chart: {
      type: "bar",
      stacked: values.stacked,
      toolbar: { show: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors,
    plotOptions: {
      bar: {
        horizontal: true,
        borderRadius: 4,
        barHeight: "60%",
        distributed: true,
        dataLabels: { total: values.total },
      },
    },
    // The per-bar labels stay off: each value is its bar's total, past the
    // bar's end on the plot, in the text colour (barLabels.js). Their colours
    // are the readable label colour of each bar's shade.
    dataLabels: {
      ...values.dataLabels,
      style: { colors: seriesLabelColors(theme, colors) },
    },
    xaxis: {
      categories,
      tickAmount: narrow ? 3 : 5,
      labels: {
        style: { colors: theme.palette.text.secondary, fontSize: "12px" },
        formatter: (val) => formatChartCost(val, { compact: narrow }),
      },
    },
    yaxis: {
      labels: {
        style: { colors: theme.palette.text.primary, fontSize: "12px" },
        maxWidth: 180,
      },
    },
    tooltip: {
      theme: theme.palette.mode,
      y: {
        formatter: (val, opts) => barTooltip(val, groups[opts?.dataPointIndex]),
      },
    },
    grid: {
      borderColor: theme.palette.divider,
      strokeDashArray: 3,
      xaxis: { lines: { show: true } },
      yaxis: { lines: { show: false } },
      padding: values.gridPadding,
    },
    legend: { show: false },
    responsive: [
      {
        breakpoint: 600,
        options: { chart: { height: 300 } },
      },
    ],
  };
}

function buildDonutChartOptions(
  theme,
  labels,
  colors,
  unpriced = 0,
  amounts = [],
) {
  return {
    chart: {
      type: "donut",
      background: "transparent",
      toolbar: { show: false },
      fontFamily: theme.typography.fontFamily,
    },
    colors,
    labels,
    dataLabels: {
      enabled: false,
    },
    plotOptions: {
      pie: {
        customScale: 0.9,
        expandOnClick: false,
        donut: {
          size: "72%",
          labels: {
            show: true,
            name: { show: false },
            // The theme's text colours: ApexCharts' default is a dark grey
            // that vanishes on the dark theme.
            value: {
              show: true,
              fontSize: "12px",
              fontWeight: 600,
              color: theme.palette.text.primary,
              // The grids' money format (grouped thousands, same digits).
              formatter: (val) => formatChartCost(val),
            },
            total: {
              show: true,
              showAlways: true,
              label: "Total",
              color: theme.palette.text.secondary,
              fontSize: "12px",
              // The slices leave out requests with no price: a floor.
              formatter: (w) =>
                costTotal(donutTotal(w.globals.seriesTotals, amounts), {
                  unpriced,
                  cell: true,
                }).text,
            },
          },
        },
      },
    },
    stroke: { width: 2, colors: [theme.palette.background.paper] },
    legend: {
      position: "bottom",
      horizontalAlign: "center",
      itemMargin: { horizontal: 10, vertical: 6 },
      labels: { colors: theme.palette.text.secondary },
      formatter: (seriesName) =>
        seriesName.length > 20 ? `${seriesName.slice(0, 20)}…` : seriesName,
    },
    tooltip: {
      theme: theme.palette.mode,
      y: {
        formatter: (val) => formatChartCost(val),
      },
    },
    responsive: [
      {
        breakpoint: 960,
        options: {
          chart: { height: 340 },
          plotOptions: {
            pie: {
              customScale: 0.82,
              donut: {
                size: "70%",
                labels: {
                  value: { fontSize: "12px" },
                  total: { fontSize: "12px" },
                },
              },
            },
          },
        },
      },
      {
        breakpoint: 600,
        options: {
          chart: { height: 300 },
          legend: { position: "bottom", fontSize: "12px" },
          plotOptions: {
            pie: {
              customScale: 0.78,
              donut: {
                labels: {
                  value: { fontSize: "12px" },
                  total: { fontSize: "12px" },
                },
              },
            },
          },
        },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const CostAnalytics = ({
  start,
  end,
  gatewayId,
  groupBy: controlledGroupBy,
  onGroupByChange,
}) => {
  const theme = useTheme();
  // Under 1200 px the bar chart is narrow: fewer, shorter value ticks.
  const narrow = useNarrowChart();
  const [localGroupBy, setLocalGroupBy] = useState("model");
  const groupBy = controlledGroupBy ?? localGroupBy;
  const setGroupBy = onGroupByChange || setLocalGroupBy;
  const groupByLabel = COST_DIMENSION_OPTIONS.find(
    (opt) => opt.value === groupBy,
  )?.label;

  const { data, isLoading, error, refetch, isFetching, isPlaceholderData } =
    useAnalyticsCost({
      start,
      end,
      groupBy,
      topN: TOP_N,
      gatewayId,
    });

  const breakdown = useMemo(() => data?.breakdown || [], [data?.breakdown]);

  const barCategories = useMemo(
    () => breakdown.map((item) => item.name || "Unknown"),
    [breakdown],
  );

  const barSeries = useMemo(
    () => [
      {
        name: "Cost",
        data: breakdown.map((item) => item.total_cost ?? 0),
      },
    ],
    [breakdown],
  );

  // Build labels and series from a single filtered list so the two arrays
  // stay aligned. Zero-cost slices are dropped — ApexCharts renders them as
  // invisible 0-degree wedges that still take up a legend slot.
  const donutSlices = useMemo(
    () =>
      breakdown
        .map((item) => ({
          name: item.name || "Unknown",
          cost: toNumericCost(item.total_cost),
          amount: item.total_cost,
        }))
        .filter((item) => item.cost > 0),
    [breakdown],
  );

  const donutLabels = useMemo(
    () => donutSlices.map((item) => item.name),
    [donutSlices],
  );

  const donutSeries = useMemo(
    () => donutSlices.map((item) => item.cost),
    [donutSlices],
  );

  // The analytics palette's purple shades (theme/data-colors.js), one per
  // group in the API's order (highest cost first), "Other" in grey. A group
  // keeps its shade in the bar and the donut (which drops 0 slices); the
  // shades no group took follow (withPaletteRest).
  const mono = useMemo(() => monoSeriesColors(theme), [theme]);
  const barColors = useMemo(
    () => withPaletteRest(colorsForNames(theme, barCategories, mono), mono),
    [theme, barCategories, mono],
  );
  const donutColors = useMemo(
    () =>
      withPaletteRest(
        donutLabels.map((name) => barColors[barCategories.indexOf(name)]),
        mono,
      ),
    [barColors, barCategories, donutLabels, mono],
  );

  const barChartOptions = useMemo(
    () =>
      buildBarChartOptions(theme, barCategories, barColors, breakdown, narrow),
    [theme, barCategories, barColors, breakdown, narrow],
  );

  // The range's requests and how many had no price (each counted 0).
  const rangeUnpriced = Number(data?.unpriced_requests) || 0;
  const rangeRequests = breakdown.reduce(
    (sum, item) => sum + (Number(item.request_count) || 0),
    0,
  );
  // No priced part: every request had no price, or every one that cost
  // anything (the rest failed or cost 0).
  const rangeUnknown = costTotal(data?.total_cost, {
    unpriced: rangeUnpriced,
    requests: rangeRequests || undefined,
  }).unknown;
  const rangeAll = rangeUnpriced >= rangeRequests;

  const donutAmounts = useMemo(
    () => donutSlices.map((item) => item.amount),
    [donutSlices],
  );

  const donutChartOptions = useMemo(
    () =>
      buildDonutChartOptions(
        theme,
        donutLabels,
        donutColors,
        rangeUnpriced,
        donutAmounts,
      ),
    [theme, donutLabels, donutColors, rangeUnpriced, donutAmounts],
  );

  const handleGroupByChange = (_event, newValue) => {
    if (newValue !== null) {
      setGroupBy(newValue);
    }
  };

  return (
    <AnalyticsQueryState
      title="Cost"
      error={error}
      hasData={data != null && !isPlaceholderData}
      isFetching={isFetching}
      onRetry={() => refetch()}
    >
      {(isFetching || isPlaceholderData) && (
        <Typography role="status" typography="s2" color="text.secondary">
          Updating Cost…
        </Typography>
      )}
      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 2,
        }}
      >
        {data != null &&
          !isPlaceholderData &&
          (rangeUnknown ? (
            <Typography typography="s1">
              {rangeAll
                ? `No price found for the ${rangeRequests.toLocaleString()} ${
                    rangeRequests === 1 ? "request" : "requests"
                  } in this range`
                : `No price found for ${rangeUnpriced.toLocaleString()} of ${rangeRequests.toLocaleString()} requests in this range`}
            </Typography>
          ) : (
            <Typography
              typography="s1"
              sx={{ fontVariantNumeric: "tabular-nums" }}
            >
              <CostTotal
                value={data?.total_cost}
                unpriced={rangeUnpriced}
                requests={rangeRequests || undefined}
                cell
              />
              <Box component="span" sx={{ color: "text.secondary" }}>
                {" spent in this range"}
                {rangeUnpriced > 0 &&
                  `, ${unpricedNote(rangeUnpriced, rangeRequests || undefined)}`}
              </Box>
            </Typography>
          ))}
        <Box sx={{ ml: { sm: "auto" } }}>
          <GroupByToggle
            value={groupBy}
            options={COST_DIMENSION_OPTIONS}
            onChange={(next) => handleGroupByChange(null, next)}
          />
        </Box>
      </Box>
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: { xs: "1fr", md: "7fr 5fr" },
        }}
      >
        <ChartPanel
          title={`Cost by ${groupByLabel}`}
          loading={isLoading || isPlaceholderData}
          height={320}
          empty={breakdown.length === 0}
          emptyText="No cost data available for this time range."
        >
          <Chart
            type="bar"
            height={Math.max(320, breakdown.length * 36)}
            options={barChartOptions}
            series={barSeries}
          />
          <Stack
            component="ul"
            aria-label="Cost group requests"
            spacing={1}
            sx={{ listStyle: "none", m: 0, p: 0 }}
          >
            {breakdown.map((group, index) => {
              const name = group.name || "Unknown";
              const link = buildLogsDrilldown({
                start,
                end,
                gatewayId,
                dimensions: { [groupBy]: group.name },
              });
              const label = link.scoped ? "View requests" : "View range";
              return (
                <Box
                  component="li"
                  key={`${name}-${index}`}
                  sx={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    gap: 2,
                    borderTop: "1px solid",
                    borderColor: "divider",
                    pt: 1,
                  }}
                >
                  <Typography
                    typography="s2"
                    sx={{ overflowWrap: "anywhere", minWidth: 0 }}
                  >
                    {name}
                  </Typography>
                  {link.href && (
                    <Link
                      href={link.href}
                      aria-label={`${label} for ${name}`}
                      title={
                        link.scoped
                          ? "Open matching requests in Logs"
                          : "This group cannot be filtered exactly. Open all requests in this time range."
                      }
                      underline="hover"
                      typography="s2"
                      sx={{ flexShrink: 0 }}
                    >
                      {label}
                    </Link>
                  )}
                </Box>
              );
            })}
          </Stack>
        </ChartPanel>
        <ChartPanel
          title="Cost distribution"
          loading={isLoading || isPlaceholderData}
          height={320}
          empty={donutSeries.length === 0}
          emptyText={
            rangeUnpriced > 0
              ? "No price found for the requests in this range."
              : "No cost data available."
          }
        >
          <Box sx={{ display: "flex", justifyContent: "center" }}>
            <Chart
              type="donut"
              height={340}
              options={donutChartOptions}
              series={donutSeries}
            />
          </Box>
        </ChartPanel>
      </Box>
    </AnalyticsQueryState>
  );
};

CostAnalytics.propTypes = {
  groupBy: PropTypes.string,
  onGroupByChange: PropTypes.func,
  granularity: PropTypes.string,
  start: PropTypes.string,
  end: PropTypes.string,
  gatewayId: PropTypes.string,
};

export default CostAnalytics;
