import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Link, Stack, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";

import { useRouteHealth } from "../hooks/useRouteHealth";
import { formatLatency, formatRate } from "./modelFacts";
import { logsLink, modelLogFilter, routeLogFilter } from "./routeHealth";
import { FACT_NUMBER, FactGrid, FactText, Section } from "./Section";

function Note({ children }) {
  return (
    <Typography typography="s2" color="text.secondary">
      {children}
    </Typography>
  );
}

Note.propTypes = { children: PropTypes.node };

const number = { ...FACT_NUMBER, flex: 0.7, minWidth: 80 };

function LogsCell({ value }) {
  return (
    <Link component={RouterLink} href={value} typography="s2">
      Open logs
    </Link>
  );
}

LogsCell.propTypes = { value: PropTypes.string };

const COLUMNS = [
  { field: "label", headerName: "Route", flex: 1.4, minWidth: 140 },
  { field: "requests", headerName: "Requests", ...number },
  {
    field: "errorRate",
    headerName: "Errors",
    valueFormatter: ({ value }) => formatRate(value),
    ...number,
  },
  {
    field: "p50",
    headerName: "p50",
    valueFormatter: ({ value }) => formatLatency(value),
    ...number,
  },
  {
    field: "p95",
    headerName: "p95",
    valueFormatter: ({ value }) => formatLatency(value),
    ...number,
  },
  {
    field: "logs",
    headerName: "Logs",
    cellRenderer: LogsCell,
    flex: 0.8,
    minWidth: 96,
  },
];

function RouteStatus({ item, logs }) {
  const { route, health, isFetching, error, refetch, dataUpdatedAt, isStale } = item;
  let status = "Waiting for traffic read…";
  if (health) status = health.requests === 0 ? "No requests" : "Traffic loaded";
  if (isFetching) status = health ? "Refreshing…" : "Loading…";
  if (error) status = "Read failed";
  return (
    <Stack
      role="group"
      aria-label={`${route.label} traffic status`}
      aria-busy={isFetching}
      direction={{ xs: "column", sm: "row" }}
      justifyContent="space-between"
      gap={1}
      sx={{ borderTop: 1, borderColor: "divider", pt: 1 }}
    >
      <Box sx={{ minWidth: 0 }}>
        <FactText strong>{route.label}</FactText>
        <FactText secondary>
          {status}
          {isStale && " · Stale — showing the last successful check."}
          {isStale && health?.requests === 0 && " No requests at that check."}
        </FactText>
        {dataUpdatedAt > 0 && (
          <FactText secondary>
            Last checked{" "}
            <time dateTime={new Date(dataUpdatedAt).toISOString()}>
              {new Date(dataUpdatedAt).toLocaleString(undefined, { timeZoneName: "short" })}
            </time>
          </FactText>
        )}
      </Box>
      <Stack direction="row" alignItems="center" gap={1} sx={{ flexShrink: 0 }}>
        {error && (
          <Button size="small" variant="outlined" disabled={isFetching} onClick={() => refetch()}>
            Retry
          </Button>
        )}
        {!(health?.requests > 0) && <LogsCell value={logs} />}
      </Stack>
    </Stack>
  );
}

RouteStatus.propTypes = {
  item: PropTypes.object.isRequired,
  logs: PropTypes.string.isRequired,
};

// Organization-local observed traffic, not a provider health verdict. A
// pending/failed route never hides the other routes' successful metrics.
export default function ModelHealth({ routes, modelId }) {
  const { items, isFetching, refreshAll } = useRouteHealth(routes, modelId);
  const rows = items
    .filter((item) => item.health?.requests > 0)
    .map(({ route, health }) => ({
      key: route.key,
      label: route.label,
      ...health,
      logs: logsLink(routeLogFilter(route, { modelId, routes })),
    }));
  const allFailed = items.length > 0 && items.every((item) => item.error && !item.health);
  const allEmpty = items.length > 0 && items.every((item) =>
    item.health?.requests === 0 && !item.error && !item.isStale && !item.isFetching);
  return (
    <Section
      title="Live health"
      note="Each route's requests from your organization through this gateway, last 24 hours."
      actions={
        <Stack direction="row" alignItems="center" flexWrap="wrap" gap={1}>
          {routes.length > 0 && (
            <Button size="small" variant="outlined" onClick={refreshAll} disabled={isFetching}>
              Refresh all
            </Button>
          )}
          <FactText sx={{ flexShrink: 0 }}>
            <Link
              component={RouterLink}
              href={logsLink(modelLogFilter(routes, modelId))}
            >
              Open logs
            </Link>
          </FactText>
        </Stack>
      }
    >
      {!routes.length && <Note>No route reaches this model, so it has no traffic.</Note>}
      {allFailed && <Note>Couldn&apos;t load this model&apos;s traffic.</Note>}
      {allEmpty && <Note>No requests to this model in the last 24 hours.</Note>}
      {rows.length > 0 && <FactGrid rowData={rows} columnDefs={COLUMNS} />}
      {routes.length > 0 && <Note>p50 and p95 are total request latency, not time to first token.</Note>}
      {items.map((item) => (
        <RouteStatus
          key={item.route.key}
          item={item}
          logs={logsLink(routeLogFilter(item.route, { modelId, routes }))}
        />
      ))}
    </Section>
  );
}

ModelHealth.propTypes = {
  routes: PropTypes.array.isRequired,
  modelId: PropTypes.string.isRequired,
};
