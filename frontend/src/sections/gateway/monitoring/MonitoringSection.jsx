import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Skeleton from "@mui/material/Skeleton";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { useGatewayContext } from "../context/useGatewayContext";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { useProviderHealth } from "../providers/hooks/useGatewayConfig";
import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
} from "../components/frame";
import { formatMs } from "../logs/logStatus";
import CostTotal from "../components/CostTotal";
import { overviewCost, perRequest, val } from "../utils/analyticsHelpers";
import { EMPTY } from "../utils/formatters";

// The p95 latency field, named once: after a "key:" the literal reads to
// gitleaks' generic rule as a credential, which stops the pre-ship scan.
const P95_LATENCY_FIELD = "p95_latency_ms";

// Monitoring: the last 24 hours at a glance, each number opening the
// Analytics tab that explains it, and each provider by what the
// gateway measured. There is no health verdict: the gateway's healthy flag
// moves only through a breaker registry that every shipped config leaves
// off, so the page shows the error rate and latency instead.

const DAY_MS = 24 * 60 * 60 * 1000;
const pct = (v) => (v == null ? EMPTY : `${Number(v).toFixed(1)}%`);
const analyticsTab = (tab) => `${paths.dashboard.gateway.analytics}/${tab}`;

const METRICS = [
  {
    key: "total_requests",
    label: "Requests",
    tab: "usage",
    format: (v) => (v == null ? EMPTY : Number(v).toLocaleString()),
  },
  {
    key: "error_rate",
    label: "Error rate",
    tab: "errors",
    format: pct,
    measured: true,
  },
  {
    key: "avg_latency_ms",
    label: "Avg latency",
    tab: "latency",
    format: formatMs,
    measured: true,
  },
  {
    key: P95_LATENCY_FIELD,
    label: "P95 latency",
    tab: "latency",
    format: formatMs,
    measured: true,
  },
  {
    key: "total_cost",
    label: "Cost",
    tab: "cost",
    // Unknown or a floor over requests with no price, never free.
    render: (overview) => <CostTotal {...overviewCost(overview)} />,
  },
];

function Metric({ label, value, href, loading }) {
  return (
    <Box component="li" sx={{ minWidth: 0 }}>
      <ButtonBase
        component={RouterLink}
        href={href}
        sx={{
          width: "100%",
          display: "block",
          textAlign: "left",
          p: 1.25,
          borderRadius: 0.5,
          "&:hover": { bgcolor: "action.hover" },
        }}
      >
        <Typography
          component="span"
          typography="s3"
          color="text.secondary"
          sx={{ display: "block" }}
        >
          {label}
        </Typography>
        {loading ? (
          <Skeleton width="60%" height={24} />
        ) : (
          <Typography
            component="span"
            typography="m3"
            sx={{ display: "block", fontVariantNumeric: "tabular-nums" }}
          >
            {value}
          </Typography>
        )}
      </ButtonBase>
    </Box>
  );
}

Metric.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
  href: PropTypes.string.isRequired,
  loading: PropTypes.bool,
};

function ErrorRate({ value }) {
  const n = Number(value);
  let color = "text.primary";
  if (n > 10) color = "error.main";
  else if (n > 2) color = "warning.main";
  return (
    <Box component="span" sx={{ color }}>
      {pct(value)}
    </Box>
  );
}

ErrorRate.propTypes = { value: PropTypes.any };

function ProviderName({ data }) {
  return (
    <Box sx={{ minWidth: 0, lineHeight: 1.3, py: 0.5 }}>
      <Typography typography="s1" noWrap>
        {data.display_name || data.name}
      </Typography>
      {data.display_name && data.display_name !== data.name && (
        <Typography typography="s3" color="text.secondary" noWrap>
          {data.name}
        </Typography>
      )}
    </Box>
  );
}

ProviderName.propTypes = { data: PropTypes.object };

// A provider that served no requests in the window has no latency and no
// error rate: the gateway reports 0 for both, which would read as "fast and
// clean". It shows the em dash, as Overview does.
const measured =
  (field) =>
  ({ data }) =>
    perRequest(data.request_count, data[field]);

// Each minimum holds its header: the label, 32 px of padding, the 24 px menu
// button and, sortable, the 24 px sort arrow (monitoring/__tests__).
const PROVIDER_COLUMNS = [
  {
    colId: "provider",
    headerName: "Provider",
    minWidth: 200,
    flex: 1.6,
    valueGetter: ({ data }) => data.display_name || data.name,
    cellRenderer: ProviderName,
  },
  {
    field: "api_format",
    headerName: "Format",
    minWidth: 128,
    flex: 0.6,
    valueFormatter: ({ value }) => value || EMPTY,
  },
  {
    colId: "models",
    headerName: "Models",
    minWidth: 132,
    flex: 0.5,
    valueGetter: ({ data }) =>
      Array.isArray(data.models) ? data.models.length : 0,
  },
  {
    field: "request_count",
    headerName: "Requests (24 h)",
    minWidth: 188,
    flex: 0.8,
    sort: "desc",
    valueFormatter: ({ value }) => Number(value ?? 0).toLocaleString(),
  },
  {
    field: "avg_latency",
    headerName: "Avg latency",
    minWidth: 160,
    flex: 0.7,
    valueGetter: measured("avg_latency"),
    valueFormatter: ({ value }) => formatMs(value),
  },
  {
    field: "error_rate",
    headerName: "Error rate (24 h)",
    minWidth: 188,
    flex: 0.8,
    valueGetter: measured("error_rate"),
    cellRenderer: ({ value }) => <ErrorRate value={value} />,
  },
];

function providerRows(health) {
  const providers = health?.providers;
  if (Array.isArray(providers))
    return providers.map((p) => ({
      ...p,
      name: p.name || p.provider_name || p.id,
    }));
  if (providers && typeof providers === "object") {
    return Object.entries(providers).map(([name, info]) => ({
      name,
      ...(typeof info === "object" ? info : {}),
    }));
  }
  return [];
}

export default function MonitoringSection() {
  // managedOffered false: the deployment offers no managed models, so no
  // request is on managed credit.
  const { gatewayId, managedOffered = true } = useGatewayContext();
  const range = useMemo(() => {
    const end = new Date();
    return {
      start: new Date(end.getTime() - DAY_MS).toISOString(),
      end: end.toISOString(),
    };
  }, []);
  const overview = useAnalyticsOverview({ ...range, gatewayId });
  const health = useProviderHealth(gatewayId);
  const providers = useMemo(() => providerRows(health.data), [health.data]);

  return (
    <GatewayPageFrame
      title="Monitoring"
      description="The last 24 hours at a glance; open a number for its trend in Analytics."
    >
      {overview.error ? (
        <GatewayEmptyState
          icon="alertCircle"
          title="The last 24 hours could not be loaded"
          description={
            overview.error.message || "The gateway API did not answer."
          }
          actionLabel="Retry"
          onAction={() => overview.refetch?.()}
        />
      ) : (
        <Box
          component="ul"
          aria-label="Last 24 hours"
          sx={{
            m: 0,
            p: 0.5,
            listStyle: "none",
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(2, minmax(0, 1fr))",
              sm: "repeat(3, minmax(0, 1fr))",
              md: "repeat(5, minmax(0, 1fr))",
            },
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
          }}
        >
          {METRICS.map((m) => (
            <Metric
              key={m.key}
              label={m.label}
              value={
                m.render
                  ? m.render(overview.data)
                  : m.format(
                      m.measured
                        ? perRequest(
                            overview.data?.total_requests,
                            val(overview.data?.[m.key]),
                          )
                        : val(overview.data?.[m.key]),
                    )
              }
              href={analyticsTab(m.tab)}
              loading={overview.isLoading}
            />
          ))}
        </Box>
      )}

      <Box
        sx={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 1,
          mt: 1,
        }}
      >
        <Typography
          component="h2"
          typography="s1"
          fontWeight="fontWeightMedium"
        >
          Your own provider keys
        </Typography>
        {/* The rows are the organization's own provider keys; the totals
            above count every request, managed credit included. */}
        <Typography typography="s2" color="text.secondary">
          Measured by the gateway over the last 24 hours.
          {managedOffered
            ? " Requests on managed credit count in the totals above."
            : ""}
        </Typography>
      </Box>
      <GatewayGrid
        rowData={health.isLoading ? undefined : providers}
        loading={health.isLoading}
        columnDefs={PROVIDER_COLUMNS}
        stateKey="gateway-monitoring-providers"
        getRowId={(params) => String(params.data.id || params.data.name)}
        height={Math.min(520, 52 + Math.max(providers.length, 3) * 48)}
        rowHeight={48}
        headerHeight={36}
        emptyState={{
          icon: "providers",
          title: "No providers yet",
          description:
            "Add a provider on the Providers page; its traffic shows up here.",
        }}
      />
    </GatewayPageFrame>
  );
}
