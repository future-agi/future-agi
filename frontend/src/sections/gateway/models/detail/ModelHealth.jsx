import React from "react";
import PropTypes from "prop-types";
import { Link, Typography } from "@mui/material";

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

// Live health: each route's requests in the last 24 hours, from this
// organization's own request logs. No provider "healthy" chip: the
// gateway has no global provider health to show. Each route opens Logs on
// its own requests (its model and provider, as counted here).
export default function ModelHealth({ routes, modelId }) {
  const { items, isLoading, error } = useRouteHealth(routes, modelId);
  const rows = items
    .filter((t) => t.health?.requests > 0)
    .map(({ route, health }) => ({
      key: route.key,
      label: route.label,
      ...health,
      logs: logsLink(routeLogFilter(route, { modelId, routes })),
    }));
  let body;
  if (!routes.length) {
    body = <Note>No route reaches this model, so it has no traffic.</Note>;
  } else if (isLoading) {
    body = <Note>Loading…</Note>;
  } else if (error) {
    body = <Note>Couldn&apos;t load this model&apos;s traffic.</Note>;
  } else if (!rows.length) {
    body = <Note>No requests to this model in the last 24 hours.</Note>;
  } else {
    body = <FactGrid rowData={rows} columnDefs={COLUMNS} />;
  }
  return (
    <Section
      title="Live health"
      note="Each route's requests from your organization through this gateway, last 24 hours."
      actions={
        <FactText sx={{ flexShrink: 0 }}>
          <Link
            component={RouterLink}
            href={logsLink(modelLogFilter(routes, modelId))}
          >
            Open logs
          </Link>
        </FactText>
      }
    >
      {body}
    </Section>
  );
}

ModelHealth.propTypes = {
  routes: PropTypes.array.isRequired,
  modelId: PropTypes.string.isRequired,
};
