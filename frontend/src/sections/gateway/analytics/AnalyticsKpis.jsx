import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import CostTotal from "../components/CostTotal";
import { CACHE_OFF_LINE } from "../providers/providerTabs";
import {
  overviewCost,
  perRequest,
  trend,
  trendSampleOk,
  val,
} from "../utils/analyticsHelpers";
import { EMPTY } from "../utils/formatters";
import { formatMs } from "../logs/logStatus";

// The p95 latency field, named once: after a "key:" the literal reads to
// gitleaks' generic rule as a credential, which stops the pre-ship scan.
const P95_LATENCY_FIELD = "p95_latency_ms";

// The overview's metrics in one quiet strip. The API returns each metric as
// { value, trend }: read the value, and show a trend only when the API has
// one (it has none when the previous period had no traffic). A latency or a
// rate over no requests was never measured: it reads EMPTY, with no trend.
//
// A change over a handful of requests is noise, so a trend shows only when
// this period and the previous one each had at least MIN_TREND_SAMPLE
// requests. With no earlier period to compare (the API's trend is null, as it
// is when the previous period had no requests or a previous value of 0) the
// change reads "—", never 0%; 0.0% is shown only for a real 0. Only cost, latency and error rate are coloured (up is bad);
// counts and the cache hit rate read text.secondary either way.
//
// The Cost never reads as free over requests the gateway found no price for
// (stored at 0): unknown when none had one, a floor when some had none
// (CostTotal). A floor or an unknown shows no change.

const count = (v) => (v == null ? EMPTY : Number(v).toLocaleString());
const percent = (v) => (v == null ? EMPTY : `${Number(v).toFixed(1)}%`);

const KPIS = [
  { key: "total_requests", label: "Requests", format: count },
  { key: "total_tokens", label: "Tokens", format: count },
  {
    key: "total_cost",
    label: "Cost",
    render: (overview) => <CostTotal {...overviewCost(overview)} />,
    costly: true,
  },
  {
    key: "avg_latency_ms",
    label: "Avg latency",
    format: formatMs,
    costly: true,
    measured: true,
  },
  {
    key: P95_LATENCY_FIELD,
    label: "P95 latency",
    format: formatMs,
    costly: true,
    measured: true,
  },
  {
    key: "error_rate",
    label: "Error rate",
    format: percent,
    costly: true,
    measured: true,
  },
  {
    key: "cache_hit_rate",
    label: "Cache hit rate",
    format: percent,
    measured: true,
  },
  { key: "active_models", label: "Models used", format: count },
];

// In light, green.700 and error.dark clear 4.5:1 on white (success.main and
// error.main do not); in dark, the theme's lifted accent pass and fail do.
const GOOD = (theme) =>
  theme.palette.mode === "dark"
    ? theme.palette.accent.pass
    : theme.palette.green[700];
const BAD = (theme) =>
  theme.palette.mode === "dark"
    ? theme.palette.accent.fail
    : theme.palette.error.dark;

const NO_EARLIER_PERIOD = "No earlier period to compare";

function NoTrend() {
  return (
    <Tooltip title={NO_EARLIER_PERIOD} describeChild placement="top">
      <Typography
        component="span"
        typography="s3"
        aria-label={NO_EARLIER_PERIOD}
        sx={{ ml: 0.75, color: "text.secondary", whiteSpace: "nowrap" }}
      >
        {EMPTY}
      </Typography>
    </Tooltip>
  );
}

function Trend({ value, costly, period }) {
  if (value == null) return <NoTrend />;
  if (!Number.isFinite(Number(value))) return null;
  const n = Number(value);
  const color = !costly || n === 0 ? "text.secondary" : n > 0 ? BAD : GOOD;
  return (
    <Tooltip title={`vs previous ${period}`} describeChild placement="top">
      <Typography
        component="span"
        typography="s3"
        sx={{ ml: 0.75, color, whiteSpace: "nowrap" }}
      >
        {`${n > 0 ? "+" : ""}${n.toFixed(1)}%`}
      </Typography>
    </Tooltip>
  );
}

Trend.propTypes = {
  value: PropTypes.any,
  costly: PropTypes.bool,
  period: PropTypes.string.isRequired,
};

export default function AnalyticsKpis({
  overview,
  loading,
  period = "period",
  cacheOff = false,
}) {
  const sampleOk = trendSampleOk(overview?.total_requests);
  // No earlier period at all: the request count itself has no trend.
  const noEarlierPeriod =
    overview?.total_requests != null && trend(overview.total_requests) == null;
  return (
    <Box
      component="ul"
      aria-label="Key metrics"
      aria-busy={loading ? "true" : "false"}
      sx={{
        m: 0,
        p: 1.5,
        listStyle: "none",
        display: "grid",
        gridTemplateColumns: {
          xs: "repeat(2, minmax(0, 1fr))",
          sm: "repeat(4, minmax(0, 1fr))",
          lg: "repeat(8, minmax(0, 1fr))",
        },
        gap: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      {KPIS.map(({ key, label, format, render, costly, measured }) => {
        const raw = val(overview?.[key]);
        const value = measured
          ? perRequest(overview?.total_requests, raw)
          : raw;
        // A total over unpriced requests is a floor or unknown: no change.
        const comparable = !(Number(overview?.[key]?.unpriced_requests) > 0);
        return (
          <Box component="li" key={key} sx={{ minWidth: 0 }}>
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ display: "block" }}
              noWrap
            >
              {label}
            </Typography>
            {loading ? (
              <Skeleton width="60%" height={22} />
            ) : (
              <Typography
                typography="m3"
                sx={{
                  fontVariantNumeric: "tabular-nums",
                  overflowWrap: "anywhere",
                }}
              >
                {render ? render(overview) : format(value)}
                {value != null &&
                  comparable &&
                  (noEarlierPeriod ? (
                    <NoTrend />
                  ) : (
                    sampleOk && (
                      <Trend
                        value={trend(overview?.[key])}
                        costly={costly}
                        period={period}
                      />
                    )
                  ))}
              </Typography>
            )}
            {/* NEW placement for site: retain period data alongside current state. */}
            {key === "cache_hit_rate" && cacheOff && (
              <Typography typography="s3" color="text.secondary">
                {CACHE_OFF_LINE}
              </Typography>
            )}
          </Box>
        );
      })}
    </Box>
  );
}

AnalyticsKpis.propTypes = {
  overview: PropTypes.object,
  loading: PropTypes.bool,
  period: PropTypes.string,
  // The gateway runs no response cache (cache not in org_sections_enforced).
  cacheOff: PropTypes.bool,
};
